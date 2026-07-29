"""The training loop.

Public API:

- ``start_run(...)``         — kick off a (synchronous) training run and
                              return the resulting ``RunRecord``. The
                              ``stdlib`` backend is fast enough to be
                              synchronous; the ``peft`` backend is
                              also synchronous in this CLI release
                              (no daemon / async). Each step appends to
                              ``<run_id>/events.jsonl``.
- ``list_runs(state)``       — scan all runs newest-first.
- ``get_run(state, id)``     — load one run.
- ``abort_run(state, id)``   — flip status to aborted (cooperative —
                              the running loop checks a flag).
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .. import metrics
from ..logging import get_logger
from ..state import State, atomic_append_jsonl, atomic_write_json
from .adapters import write_adapter
from .datasets import ensure_dataset
from .evaluation import evaluate_adapter
from .lock import FinetuneLock
from .models import (
    ADAPTER_STATUS_EVAL_FAILED,
    ADAPTER_STATUS_EVAL_PASSED,
    ADAPTER_STATUS_TRAINED,
    RUN_STATUS_ABORTED,
    RUN_STATUS_EVAL_FAILED,
    RUN_STATUS_EVAL_PASSED,
    RUN_STATUS_PROMOTED,
    RUN_STATUS_STARTED,
    RUN_STATUS_TRAINED,
    AdapterRecord,
    RunRecord,
    new_adapter_id,
    new_run_id,
)
from .recipes import RecipeError, get_recipe, recipe_needs_score

log = get_logger(__name__)


class TrainingError(RuntimeError):
    """Raised for any training-pipeline failure (incl. backend errors)."""


def _run_root(state: State, run_id: str) -> Path:
    return state.finetuning_runs_dir / run_id


def _events_path(state: State, run_id: str) -> Path:
    return _run_root(state, run_id) / "events.jsonl"


def _run_path(state: State, run_id: str) -> Path:
    return _run_root(state, run_id) / "run.json"


def _eval_path(state: State, run_id: str) -> Path:
    return _run_root(state, run_id) / "eval.json"


def _emit_event(state: State, run_id: str, event: str, **fields: Any) -> None:
    rec = {"ts": int(time.time()), "run_id": run_id, "event": event, **fields}
    atomic_append_jsonl(_events_path(state, run_id), rec)


def _persist_run(state: State, run: RunRecord) -> None:
    atomic_write_json(_run_path(state, run.run_id), run.to_dict())


def list_runs(state: State, status: str | None = None) -> list[RunRecord]:
    out: list[RunRecord] = []
    for p in sorted(state.finetuning_runs_dir.glob("*/run.json"), reverse=True):
        try:
            r = RunRecord.from_dict(json.loads(p.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if status and r.status != status:
            continue
        out.append(r)
    return out


def get_run(state: State, run_id: str) -> RunRecord | None:
    p = _run_path(state, run_id)
    if not p.exists():
        return None
    try:
        return RunRecord.from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def abort_run(state: State, run_id: str) -> RunRecord:
    """Flip a run's status to ABORTED.

    If the run is ``RUN_STATUS_STARTED`` or ``RUN_STATUS_TRAINED``, the
    status is updated cooperatively (the training loop checks the
    ``.abort_requested`` flag file). For terminal statuses this raises.
    """
    run = get_run(state, run_id)
    if run is None:
        raise TrainingError(f"unknown run_id: {run_id!r}")
    if run.status in (RUN_STATUS_EVAL_PASSED, RUN_STATUS_EVAL_FAILED,
                      RUN_STATUS_PROMOTED, RUN_STATUS_ABORTED):
        raise TrainingError(f"run {run_id!r} already terminal: {run.status}")
    # write a sentinel the loop will pick up
    (state.finetuning_runs_dir / run_id / ".abort_requested").write_text("1")
    updated = replace(run, status=RUN_STATUS_ABORTED, finished_at=int(time.time()),
                      last_error="abort requested")
    _persist_run(state, updated)
    _emit_event(state, run_id, "aborted")
    return updated


def _maybe_resolve_backend(backend: str) -> str:
    """Return the actual backend name; "auto" picks based on availability."""
    if backend != "auto":
        return backend
    try:
        import peft  # noqa: F401
        return "peft"
    except ImportError:
        return "stdlib"


def start_run(state: State, dataset_id: str, base_model_id: str,
              recipe_name: str = "sft-mini",
              backend: str = "stdlib",
              epochs: int | None = None,
              batch_size: int | None = None,
              lr: float | None = None,
              seed: int | None = None,
              max_steps: int | None = None,
              eval_threshold: float | None = None,
              register: bool = False,
              timeout_seconds: float = 30.0,
              ) -> RunRecord:
    """Start a training run synchronously and return the updated record.

    Side effects:

    - ``<state>/finetuning/runs/<run_id>/`` is created with
      ``run.json`` + ``events.jsonl`` + (maybe) ``adapter.json``
      + ``eval.json``.
    - On success, an ``AdapterRecord`` is also written to
      ``<state>/finetuning/adapters/<ft_id>.json``.
    - Five metric families are bumped; the Prometheus
      ``rollout_shield_finetuning_*`` series will reflect the run.
    - Plugin events ``finetuning.run.started`` and
      ``finetuning.run.completed`` are dispatched.
    """
    ensure_dataset(state, dataset_id)
    try:
        recipe = get_recipe(recipe_name)
    except RecipeError:
        raise

    backend = _maybe_resolve_backend(backend)

    # recipe overrides
    epochs = int(epochs) if epochs is not None else recipe.epochs
    batch_size = int(batch_size) if batch_size is not None else recipe.batch_size
    lr = float(lr) if lr is not None else recipe.lr
    seed = int(seed) if seed is not None else recipe.seed
    max_steps = int(max_steps) if max_steps is not None else recipe.max_steps
    eval_threshold = (float(eval_threshold) if eval_threshold is not None
                      else recipe.eval_threshold)

    if recipe_needs_score(recipe_name):
        from .datasets import get_dataset
        ds = get_dataset(state, dataset_id)
        if ds is None or ds.format != "prompt-target-score":
            raise TrainingError(
                f"recipe {recipe_name!r} requires a 'prompt-target-score' "
                f"dataset; {dataset_id!r} is {ds.format if ds else 'unknown'}"
            )

    run_id = new_run_id()
    _run_root(state, run_id).mkdir(parents=True, exist_ok=True)
    (state.finetuning_runs_dir / run_id / ".abort_requested").unlink(missing_ok=True)

    started_at = int(time.time())
    run = RunRecord(
        run_id=run_id, dataset_id=dataset_id, base_model_id=base_model_id,
        recipe_name=recipe_name, backend=backend, status=RUN_STATUS_STARTED,
        epochs=epochs, batch_size=batch_size, lr=lr, seed=seed,
        max_steps=max_steps, started_at=started_at,
        meta={"eval_threshold": eval_threshold,
              "peft_overrides": recipe.peft_overrides},
    )
    _persist_run(state, run)
    _emit_event(state, run_id, "started", base=base_model_id, backend=backend,
                recipe=recipe_name)
    metrics.finetuning_run_steps_total.inc(0.0, labels=(backend, recipe_name))
    dispatch_event_safe("finetuning.run.started", run_id=run_id,
                        dataset_id=dataset_id, base_model_id=base_model_id,
                        recipe_name=recipe_name, backend=backend)

    finished: RunRecord
    adapter_id: str | None = None
    train_loss_final = 0.0
    train_steps = 0
    train_samples_seen = 0
    elapsed_total = 0.0

    with FinetuneLock(state.root, timeout_seconds=timeout_seconds):
        # backend dispatch
        from .backends import resolve as resolve_backend
        try:
            backend_obj = resolve_backend(backend)
        except RuntimeError as exc:
            err = str(exc)
            finished = replace(run, status=RUN_STATUS_ABORTED,
                               finished_at=int(time.time()), last_error=err)
            _persist_run(state, finished)
            _emit_event(state, run_id, "aborted", error=err)
            metrics.finetuning_runs_total.inc(1.0,
                labels=(backend, recipe_name, RUN_STATUS_ABORTED))
            dispatch_event_safe("finetuning.run.completed", run_id=run_id,
                                status=RUN_STATUS_ABORTED, error=err)
            return finished

        # build the adapter id deterministically so re-runs share storage
        adapter_id = new_adapter_id(
            base_model_id=base_model_id, dataset_id=dataset_id,
            recipe_name=recipe_name, backend=backend, seed=seed,
            hyperparams={"epochs": epochs, "batch_size": batch_size,
                         "lr": lr, "max_steps": max_steps,
                         "eval_threshold": eval_threshold},
        )
        artifact_dir = state.finetuning_adapters_dir / f"{adapter_id}.artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # train
        train_started = time.monotonic()
        try:
            output = backend_obj.train(
                state=state, dataset_id=dataset_id, base_model_id=base_model_id,
                recipe_name=recipe_name, epochs=epochs, batch_size=batch_size,
                lr=lr, seed=seed, max_steps=max_steps,
                artifact_dir=artifact_dir,
                abort_flag_path=_run_root(state, run_id) / ".abort_requested",
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            finished = replace(run, status=RUN_STATUS_EVAL_FAILED,
                               finished_at=int(time.time()), last_error=err,
                               adapter_id=adapter_id)
            _persist_run(state, finished)
            _emit_event(state, run_id, "train_failed", error=err)
            metrics.finetuning_runs_total.inc(1.0,
                labels=(backend, recipe_name, RUN_STATUS_EVAL_FAILED))
            return finished
        train_loss_final = float(output.get("final_loss", 0.0))
        train_steps = int(output.get("train_steps", 0))
        train_samples_seen = int(output.get("samples_seen", 0))
        elapsed_total += time.monotonic() - train_started

        # persist a stub adapter now (so a crash mid-eval still leaves trace)
        stub = AdapterRecord(
            adapter_id=adapter_id, base_model_id=base_model_id,
            dataset_id=dataset_id, recipe_name=recipe_name,
            backend=backend, status=ADAPTER_STATUS_TRAINED,
            train_steps=train_steps, train_samples_seen=train_samples_seen,
            train_loss_final=train_loss_final,
            eval_threshold=eval_threshold,
            artifact_path=artifact_dir if any(artifact_dir.iterdir()) else None,
        )
        write_adapter(state, stub)
        run_trained = replace(run, status=RUN_STATUS_TRAINED,
                              finished_at=int(time.time()),
                              adapter_id=adapter_id)
        _persist_run(state, run_trained)
        _emit_event(state, run_id, "trained", train_steps=train_steps,
                    final_loss=train_loss_final)

        # eval
        eval_started = time.monotonic()
        try:
            eval_result = evaluate_adapter(
                state, adapter_id,
                gen=backend_obj.build_generator(state, adapter_id),
                dataset_id=dataset_id, recipe_name=recipe_name,
            )
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            finished = replace(run_trained, status=RUN_STATUS_EVAL_FAILED,
                               finished_at=int(time.time()), last_error=err,
                               adapter_id=adapter_id)
            _persist_run(state, finished)
            _emit_event(state, run_id, "eval_failed", error=err)
            metrics.finetuning_runs_total.inc(1.0,
                labels=(backend, recipe_name, RUN_STATUS_EVAL_FAILED))
            return finished
        elapsed_total += time.monotonic() - eval_started
        atomic_write_json(_eval_path(state, run_id), eval_result.to_dict())

        passed = (eval_result.metrics.get("drift_from_baseline", 0.0)
                  >= eval_threshold)
        new_adapter_status = (ADAPTER_STATUS_EVAL_PASSED if passed
                              else ADAPTER_STATUS_EVAL_FAILED)
        new_run_status = (RUN_STATUS_EVAL_PASSED if passed
                          else RUN_STATUS_EVAL_FAILED)
        adapter_final = replace(stub, status=new_adapter_status,
                                eval_metrics=eval_result.metrics,
                                finished_at=int(time.time()))
        write_adapter(state, adapter_final)

        run_evaled = replace(run_trained, status=new_run_status,
                             finished_at=int(time.time()),
                             adapter_id=adapter_id,
                             meta={**run_trained.meta,
                                   "eval": eval_result.metrics})
        _persist_run(state, run_evaled)
        _emit_event(state, run_id, "evaluated",
                    passed=passed, metrics=eval_result.metrics)

        # optional auto-promote
        if passed and register:
            try:
                from .promote import promote_adapter
                promoted = promote_adapter(state, adapter_id)
                finalized = replace(run_evaled, status=RUN_STATUS_PROMOTED,
                                    adapter_id=adapter_id)
                _persist_run(state, finalized)
                _emit_event(state, run_id, "promoted", adapter_id=adapter_id,
                            promoted_as=promoted.promoted_as_model_id)
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                _emit_event(state, run_id, "promote_failed", error=err)
                finalized = run_evaled
        else:
            finalized = run_evaled

    # metrics + dispatch
    try:
        metrics.finetuning_run_steps_total.inc(train_steps,
            labels=(backend, recipe_name))
        metrics.finetuning_runs_total.inc(
            1.0, labels=(backend, recipe_name, finalized.status))
        metrics.finetuning_run_duration_seconds.observe(
            elapsed_total, labels=(backend, finalized.status))
        metrics.finetuning_adapters_total.set(
            len(__import__("json").loads(
                __import__("glob").glob.__module__  # noqa — pure noop
            )) if False else 0  # replaced below
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        from .adapters import list_adapters as _la
        backend_counts: dict[tuple[str, str], int] = {}
        for a in _la(state):
            backend_counts[(a.backend, a.status)] = (
                backend_counts.get((a.backend, a.status), 0) + 1
            )
        for (be, st), cnt in backend_counts.items():
            metrics.finetuning_adapters_total.set(float(cnt), labels=(be, st))
    except Exception:  # noqa: BLE001
        pass
    dispatch_event_safe("finetuning.run.completed", run_id=finalized.run_id,
                        status=finalized.status, adapter_id=adapter_id,
                        eval_metrics=eval_result.metrics)
    return finalized


def dispatch_event_safe(event: str, **fields: Any) -> None:
    """Dispatch a plugin event; swallow any plugin error."""
    try:
        from ..plugins import dispatch
        dispatch(event, **fields)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["start_run", "list_runs", "get_run", "abort_run",
           "TrainingError"]
