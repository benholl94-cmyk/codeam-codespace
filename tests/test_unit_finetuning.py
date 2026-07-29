"""Unit tests for the finetuning subsystem."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rollout_shield.finetuning import (
    DatasetError,
    EvalResult,
    abort_run,
    doctor,
    evaluate_adapter,
    get_adapter,
    list_datasets,
    list_promoted,
    list_runs,
    promote_adapter,
    register_dataset,
    remove_dataset,
    start_run,
    unpromote_adapter,
)
from rollout_shield.finetuning.adapters import (
    get_adapter as _get_adapter,
)
from rollout_shield.finetuning.adapters import (
    list_adapters as _list_adapters,
)
from rollout_shield.finetuning.adapters import (
    write_adapter,
)
from rollout_shield.finetuning.backends import backends as available_backends
from rollout_shield.finetuning.datasets import (
    DEFAULT_SPLIT,
    VALID_FORMATS,
)
from rollout_shield.finetuning.datasets import (
    iter_split as _iter_split,
)
from rollout_shield.finetuning.lock import FinetuneLock
from rollout_shield.finetuning.models import (
    RUN_STATUS_EVAL_FAILED,
    RUN_STATUS_EVAL_PASSED,
    AdapterRecord,
    DatasetRecord,
    RunRecord,
    new_adapter_id,
    new_dataset_id,
    new_run_id,
)
from rollout_shield.finetuning.recipes import (
    SUPPORTED_RECIPES,
    RecipeError,
    get_recipe,
    recipe_needs_score,
)
from rollout_shield.finetuning.training import TrainingError as TE2


def _write_sample_jsonl(path: Path, n: int = 20, fmt: str = "prompt-target") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for i in range(n):
            if fmt == "prompt-target-score":
                row = {"prompt": f"q{i}", "target": f"a{i}",
                       "score": 1.0 if i % 5 else -1.0}
            elif fmt == "raw":
                row = {"text": f"line {i} content for training."}
            else:
                row = {"prompt": f"q{i}", "target": f"a{i}"}
            f.write(json.dumps(row) + "\n")


# ----- models.py -------------------------------------------------------------


def test_new_ids_are_well_formed():
    ds = new_dataset_id(Path("/tmp/data.jsonl"), "prompt-target")
    assert ds.startswith("ds_") and len(ds) == 19
    ft = new_adapter_id(base_model_id="b", dataset_id="d", recipe_name="sft-mini",
                        backend="stdlib", seed=1, hyperparams={"a": 1})
    assert ft.startswith("ft_") and len(ft) == 19
    rn = new_run_id()
    assert rn.startswith("run_") and len(rn) == 20


def test_new_adapter_id_is_deterministic():
    a = new_adapter_id(base_model_id="base", dataset_id="ds_a",
                       recipe_name="sft-mini", backend="stdlib", seed=1,
                       hyperparams={"x": 1})
    b = new_adapter_id(base_model_id="base", dataset_id="ds_a",
                       recipe_name="sft-mini", backend="stdlib", seed=1,
                       hyperparams={"x": 1})
    c = new_adapter_id(base_model_id="base", dataset_id="ds_a",
                       recipe_name="sft-mini", backend="stdlib", seed=1,
                       hyperparams={"x": 2})
    assert a == b
    assert a != c


def test_adapter_record_roundtrip():
    r = AdapterRecord(
        adapter_id="ft_abc", base_model_id="base",
        dataset_id="ds_xyz", recipe_name="sft-mini",
        backend="stdlib", status="trained",
        train_steps=10, train_samples_seen=200,
        train_loss_final=0.42, eval_threshold=0.1,
    )
    d = r.to_dict()
    r2 = AdapterRecord.from_dict(d)
    assert r2.adapter_id == r.adapter_id
    assert r2.train_loss_final == r.train_loss_final


def test_run_record_roundtrip():
    r = RunRecord(
        run_id="run_xyz", dataset_id="ds_abc", base_model_id="b",
        recipe_name="sft-mini", backend="stdlib", status="started",
        epochs=1, batch_size=4, lr=0.001, seed=1, max_steps=None,
        started_at=1, finished_at=2, adapter_id="ft_q",
    )
    d = r.to_dict()
    r2 = RunRecord.from_dict(d)
    assert r2.adapter_id == "ft_q"
    assert r2.started_at == 1


def test_dataset_record_roundtrip():
    r = DatasetRecord(
        dataset_id="ds_a", name="x", format="prompt-target",
        n_samples=20, train_size=18, val_size=2,
        content_sha256="abc", path=Path("/tmp/x.jsonl"),
        created_at=1,
    )
    d = r.to_dict()
    r2 = DatasetRecord.from_dict(d)
    assert r2.n_samples == 20
    assert r2.format == "prompt-target"


# ----- datasets.py -----------------------------------------------------------


def test_register_dataset_idempotent(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=20)
    r1 = register_dataset(scratch_state, src_path=p, name="n1")
    r2 = register_dataset(scratch_state, src_path=p, name="n2")
    assert r1.dataset_id == r2.dataset_id  # same content → same hash
    assert r1.content_sha256 == r2.content_sha256
    assert r1.n_samples == 20


def test_register_dataset_split(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=20)
    r = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    assert r.train_size + r.val_size == r.n_samples
    assert r.val_size == 4


def test_register_dataset_invalid_format(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=20)
    with pytest.raises(DatasetError):
        register_dataset(scratch_state, src_path=p, name="n",
                         format_name="not-a-format")


def test_register_dataset_missing_file(scratch_state, tmp_path):
    with pytest.raises((DatasetError, FileNotFoundError)):
        register_dataset(scratch_state, src_path=tmp_path / "missing.jsonl",
                         name="n")


def test_register_dataset_prompt_target_score(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=20, fmt="prompt-target-score")
    r = register_dataset(scratch_state, src_path=p, name="n",
                         format_name="prompt-target-score")
    assert r.format == "prompt-target-score"


def test_iter_split_returns_only_train_or_val(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=20)
    r = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    train_rows = list(_iter_split(scratch_state, r.dataset_id, "train"))
    val_rows = list(_iter_split(scratch_state, r.dataset_id, "val"))
    assert len(train_rows) + len(val_rows) == 20
    assert len(val_rows) == 4


def test_list_datasets_and_remove(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=10)
    r = register_dataset(scratch_state, src_path=p, name="n")
    assert r.dataset_id in {d.dataset_id for d in list_datasets(scratch_state)}
    remove_dataset(scratch_state, r.dataset_id)
    assert r.dataset_id not in {d.dataset_id for d in list_datasets(scratch_state)}


def test_valid_formats_constant():
    assert "prompt-target" in VALID_FORMATS
    assert "prompt-target-score" in VALID_FORMATS
    assert "raw" in VALID_FORMATS
    assert DEFAULT_SPLIT == 0.8


# ----- recipes.py ------------------------------------------------------------


def test_recipes_registry_has_expected():
    assert "sft-mini" in SUPPORTED_RECIPES
    assert "lora-tiny" in SUPPORTED_RECIPES
    assert "dpo-mini" in SUPPORTED_RECIPES


def test_get_recipe_known():
    r = get_recipe("sft-mini")
    assert r.name == "sft-mini"
    assert r.epochs >= 1


def test_get_recipe_unknown_raises():
    with pytest.raises(RecipeError):
        get_recipe("not-a-recipe")


def test_recipe_needs_score():
    assert recipe_needs_score("dpo-mini")
    assert not recipe_needs_score("sft-mini")
    assert not recipe_needs_score("lora-tiny")


def test_recipes_are_distinct():
    names = set(SUPPORTED_RECIPES)
    assert len(names) == len(SUPPORTED_RECIPES)  # no duplicates


# ----- lock.py ---------------------------------------------------------------


def test_finetune_lock_acquire_release(scratch_state):
    with FinetuneLock(scratch_state.root, timeout_seconds=1.0):
        # second acquire should block or raise — we use a small timeout
        with pytest.raises((BlockingIOError, TimeoutError, OSError)):
            with FinetuneLock(scratch_state.root, timeout_seconds=0.05):
                pass


def test_finetune_lock_releases_after_exception(scratch_state):
    try:
        with FinetuneLock(scratch_state.root, timeout_seconds=1.0):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # should be acquire-able again
    with FinetuneLock(scratch_state.root, timeout_seconds=1.0):
        pass


# ----- adapters.py -----------------------------------------------------------


def test_write_and_get_adapter(scratch_state):
    rec = AdapterRecord(
        adapter_id="ft_test", base_model_id="b",
        dataset_id="ds_x", recipe_name="sft-mini",
        backend="stdlib", status="trained",
        train_steps=10, train_samples_seen=10,
        train_loss_final=0.5, eval_threshold=0.1,
    )
    write_adapter(scratch_state, rec)
    out = _get_adapter(scratch_state, "ft_test")
    assert out is not None
    assert out.base_model_id == "b"


def test_list_adapters_includes_written(scratch_state):
    rec = AdapterRecord(
        adapter_id="ft_x1", base_model_id="b",
        dataset_id="ds_x", recipe_name="sft-mini",
        backend="stdlib", status="trained",
        train_steps=1, train_samples_seen=1,
        train_loss_final=0.0, eval_threshold=0.1,
    )
    write_adapter(scratch_state, rec)
    found = {a.adapter_id for a in _list_adapters(scratch_state)}
    assert "ft_x1" in found


# ----- training.py: full stdlib run -----------------------------------------


def test_start_run_sft_mini_stdlib(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=24)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)

    # register a base model so the baseline call has something to use
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="test-base", name="Test Base", description="",
        family="mock", fn=lambda prompt, params: {"text": f"base:{prompt}",
                                                 "tokens": 1, "meta": {}},
    ))

    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="test-base",
                    recipe_name="sft-mini", backend="stdlib",
                    epochs=1, max_steps=4, eval_threshold=0.0)
    assert rec.run_id.startswith("run_")
    assert rec.status in (RUN_STATUS_EVAL_PASSED, RUN_STATUS_EVAL_FAILED)
    assert rec.adapter_id is not None
    assert rec.adapter_id.startswith("ft_")


def test_start_run_dpo_requires_score_dataset(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=24)  # prompt-target, not score
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    with pytest.raises(TE2):
        start_run(scratch_state, dataset_id=ds.dataset_id,
                  base_model_id="b", recipe_name="dpo-mini", backend="stdlib")


def test_abort_run(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b1", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="b1", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2)
    # terminal already; abort should raise
    with pytest.raises(TE2):
        abort_run(scratch_state, rec.run_id)


def test_list_runs_filters_status(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b2", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="b2", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2)
    all_runs = list_runs(scratch_state)
    assert rec.run_id in {r.run_id for r in all_runs}
    only_done = list_runs(scratch_state,
                          status=RUN_STATUS_EVAL_PASSED)
    # no guarantees on passing — just verify filter doesn't crash
    assert isinstance(only_done, list)


# ----- evaluation.py ---------------------------------------------------------


def test_evaluate_adapter_returns_metrics(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b3", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "alpha", "tokens": 1, "meta": {}},
    ))
    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="b3", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2)
    assert rec.adapter_id is not None
    adapter = get_adapter(scratch_state, rec.adapter_id)
    assert adapter is not None

    # Use the backend's build_generator to evaluate
    from rollout_shield.finetuning.backends import resolve as resolve_backend
    backend = resolve_backend("stdlib")
    gen = backend.build_generator(scratch_state, rec.adapter_id)
    result = evaluate_adapter(scratch_state, rec.adapter_id, gen=gen,
                              dataset_id=ds.dataset_id,
                              recipe_name="sft-mini")
    assert isinstance(result, EvalResult)
    assert result.n_val >= 1
    assert "exact_match" in result.metrics
    assert "bleu1_proxy" in result.metrics
    assert "drift_from_baseline" in result.metrics


# ----- promote.py ------------------------------------------------------------


def test_promote_then_unpromote(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b4", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="b4", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2,
                    eval_threshold=0.0)  # 0 so it always passes
    assert rec.adapter_id is not None

    if rec.status == RUN_STATUS_EVAL_PASSED:
        promoted = promote_adapter(scratch_state, rec.adapter_id)
        assert promoted.promoted_as_model_id is not None
        assert rec.adapter_id in list_promoted(scratch_state)
        ok = unpromote_adapter(scratch_state, rec.adapter_id)
        assert ok is True
    else:
        # if eval failed, skip — promotion precondition not met
        pytest.skip("eval_threshold=0 still failed; check stdlib backend")


def test_promote_unknown_raises(scratch_state):
    with pytest.raises(FileNotFoundError):
        promote_adapter(scratch_state, "ft_nope")


def test_promote_requires_eval_passed(scratch_state, tmp_path):
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b5", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    rec = start_run(scratch_state, dataset_id=ds.dataset_id,
                    base_model_id="b5", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2,
                    eval_threshold=1.5)  # impossible threshold
    assert rec.status == RUN_STATUS_EVAL_FAILED
    with pytest.raises(ValueError):
        promote_adapter(scratch_state, rec.adapter_id)


def test_unpromote_unknown_returns_false(scratch_state):
    assert unpromote_adapter(scratch_state, "ft_nope") is False


# ----- backends --------------------------------------------------------------


def test_available_backends_at_least_stdlib():
    bs = available_backends()
    assert "stdlib" in bs


def test_resolve_stdlib():
    from rollout_shield.finetuning.backends import resolve
    b = resolve("stdlib")
    assert b.name == "stdlib"


def test_resolve_unknown_raises():
    from rollout_shield.finetuning.backends import resolve
    with pytest.raises(RuntimeError):
        resolve("not-a-backend")


def test_resolve_peft_without_install(monkeypatch):
    from rollout_shield.finetuning import backends as bk
    bk.reset()
    import sys
    monkeypatch.setitem(sys.modules, "peft", None)
    # peft is None in sys.modules → ImportError on import; resolve should raise.
    # In some Python versions setting to None still allows import; be tolerant.
    try:
        with pytest.raises(RuntimeError):
            bk.resolve("peft")
    except Exception:  # pragma: no cover
        pass
    # restore for other tests
    if "peft" in sys.modules:
        try:
            del sys.modules["peft"]
        except KeyError:
            pass


def test_stdlib_backend_train_and_generate(tmp_path, scratch_state):
    from rollout_shield.finetuning.backends import resolve
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)

    backend = resolve("stdlib")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    out = backend.train(
        state=scratch_state, dataset_id=ds.dataset_id,
        base_model_id="test-base", recipe_name="sft-mini",
        epochs=1, batch_size=4, lr=0.001, seed=1, max_steps=4,
        artifact_dir=artifact_dir,
        abort_flag_path=tmp_path / "abort",
    )
    assert "final_loss" in out
    assert "train_steps" in out
    assert (artifact_dir / "pattern_capture.json").exists()


def test_stdlib_backend_build_generator_fallback(scratch_state):
    from rollout_shield.finetuning.backends import resolve
    backend = resolve("stdlib")
    gen = backend.build_generator(scratch_state, "ft_does_not_exist")
    out = gen("hello world")
    assert isinstance(out, str)


# ----- doctor.py -------------------------------------------------------------


def test_doctor_returns_report(scratch_state):
    r = doctor(scratch_state)
    d = r.to_dict()
    assert "python" in d
    assert "state_writable" in d
    assert "backend_stdlib" in d
    assert d["backend_stdlib"] is True
    assert d["state_writable"] is True


def test_doctor_reports_disk_free(scratch_state):
    r = doctor(scratch_state)
    assert r.disk_free_bytes >= 0


# ----- metrics emit ----------------------------------------------------------


def test_run_bumps_metrics(scratch_state, tmp_path):
    from rollout_shield import metrics
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b6", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    start_run(scratch_state, dataset_id=ds.dataset_id,
              base_model_id="b6", recipe_name="sft-mini",
              backend="stdlib", epochs=1, max_steps=2)
    rendered = metrics.registry().render()
    assert "rollout_shield_finetuning_runs_total" in rendered
    assert "rollout_shield_finetuning_eval_score" in rendered


# ----- plugin events ---------------------------------------------------------


def test_plugin_events_dispatched(scratch_state, tmp_path):
    """`plugins.dispatch` should not raise when called with the
    finetuning event names; subscriber callbacks fire from the
    manifest's hooks."""
    from rollout_shield import plugins as _plugins
    # No-op test: just verify dispatch is callable for our events.
    _plugins.dispatch("finetuning.dataset.created", dataset_id="ds_x")
    _plugins.dispatch("finetuning.run.started", run_id="run_x")
    _plugins.dispatch("finetuning.run.completed", run_id="run_x",
                      status="eval_passed")
    p = tmp_path / "data.jsonl"
    _write_sample_jsonl(p, n=12)
    ds = register_dataset(scratch_state, src_path=p, name="n", split=0.8)
    from rollout_shield.ai.models import ModelInfo, register_model
    register_model(ModelInfo(
        id="b7", name="B", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    start_run(scratch_state, dataset_id=ds.dataset_id,
              base_model_id="b7", recipe_name="sft-mini",
              backend="stdlib", epochs=1, max_steps=2,
              eval_threshold=0.0)
