"""Adapter promotion: register an adapter as a routable model.

Promotion does two things:

1. Writes the adapter id into ``<state>/finetuning/promoted.json``
   (a JSON list of promoted adapter ids).
2. Registers a new model entry with ``ai.models.register_model`` with
   ``family="finetuned"`` and ``id=f"{base_model_id}-ft-{short8}"``. The
   registered ``fn`` delegates to the backend's ``build_generator``.

When a fresh process imports ``ai.models``, the same sidecar is read
and the adapters are re-registered so they survive a restart.

The routed model id format keeps the base id visible, plus a short
adapter fingerprint, so a single human can see both lineage and which
specific adapter it is.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .. import metrics
from ..state import State, atomic_write_json

if TYPE_CHECKING:
    from .adapters import AdapterRecord

PROMOTED_FILENAME = "promoted.json"


def _promoted_path(state: State) -> Path:
    return state.finetuning_dir / PROMOTED_FILENAME


def _load_promoted(state: State) -> list[str]:
    p = _promoted_path(state)
    if not p.exists():
        return []
    try:
        v = json.loads(p.read_text())
        return [str(x) for x in v] if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def _save_promoted(state: State, ids: list[str]) -> None:
    # stable, sorted, dedup
    out = sorted(set(ids))
    atomic_write_json(_promoted_path(state), out)


def list_promoted(state: State) -> list[str]:
    return _load_promoted(state)


def is_promoted(state: State, adapter_id: str) -> bool:
    return adapter_id in _load_promoted(state)


def _promote_model_id(base_model_id: str, adapter_id: str) -> str:
    short = adapter_id.replace("ft_", "")[:8]
    return f"{base_model_id}-ft-{short}"


def _emit_safe_register(info) -> None:
    try:
        from ..ai.models import register_model
        register_model(info)
    except Exception:
        pass


def promote_adapter(state: State, adapter_id: str) -> AdapterRecord:
    """Register the adapter's underlying base as a routable model.

    The new model id is ``{base}-ft-{8hex}``. The adapter manifest is
    rewritten with ``promoted_as_model_id`` set and
    ``status=ADAPTER_STATUS_PROMOTED``.

    Idempotent: promoting an already-promoted adapter returns the same
    record.
    """
    from .adapters import get_adapter, write_adapter
    from .models import (
        ADAPTER_STATUS_EVAL_PASSED,
        ADAPTER_STATUS_PROMOTED,
    )

    adapter = get_adapter(state, adapter_id)
    if adapter is None:
        raise FileNotFoundError(f"unknown adapter_id: {adapter_id!r}")
    if adapter.status not in (ADAPTER_STATUS_EVAL_PASSED,
                              ADAPTER_STATUS_PROMOTED):
        raise ValueError(
            f"adapter {adapter_id!r} cannot be promoted "
            f"(status={adapter.status}); eval_passed required"
        )

    promoted_ids = _load_promoted(state)
    if adapter_id in promoted_ids and adapter.promoted_as_model_id:
        return adapter

    model_id = _promote_model_id(adapter.base_model_id, adapter_id)

    # Build the ModelFn closure that delegates to the backend
    def _fn(prompt: str, params: dict) -> dict:
        from .backends import resolve as resolve_backend
        backend = resolve_backend(adapter.backend)
        gen = backend.build_generator(state, adapter_id)
        try:
            text = gen(prompt) or ""
        except Exception as exc:  # noqa: BLE001
            return {"text": "", "tokens": 0, "meta": {"error": str(exc)}}
        return {"text": text, "tokens": len(text.split()),
                "meta": {"adapter_id": adapter_id,
                         "base_model_id": adapter.base_model_id,
                         "backend": adapter.backend,
                         "real": adapter.backend != "stdlib"}}

    # Try to register on the live AI registry (in-process); the
    # sidecar file is the durable record.
    try:
        from ..ai.models import ModelInfo, register_model
        register_model(ModelInfo(
            id=model_id, name=f"{adapter.base_model_id} (finetuned {adapter_id})",
            description=f"Finetuned adapter over {adapter.base_model_id} "
                        f"via {adapter.backend} backend",
            family="finetuned",
            fn=_fn,
            cost_per_1k_tokens=0.0,
            meta={"adapter_id": adapter_id, "base_model_id": adapter.base_model_id,
                  "backend": adapter.backend, "recipe": adapter.recipe_name},
        ))
    except Exception:  # noqa: BLE001
        # AI layer not importable in some unit tests; the sidecar is
        # still written, and ``re_register_promoted`` will replay it.
        pass

    promoted_ids.append(adapter_id)
    _save_promoted(state, promoted_ids)

    updated = adapter.__class__(
        **{**adapter.__dict__,
           "status": ADAPTER_STATUS_PROMOTED,
           "promoted_as_model_id": model_id,
           "finished_at": int(__import__("time").time())},
    )
    write_adapter(state, updated)
    try:
        metrics.finetuning_promoted_total.set(float(len(_load_promoted(state))))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..plugins import dispatch
        dispatch("finetuning.adapter.promoted",
                 adapter_id=adapter_id,
                 promoted_as_model_id=model_id,
                 base_model_id=adapter.base_model_id)
    except Exception:  # noqa: BLE001
        pass
    return updated


def unpromote_adapter(state: State, adapter_id: str) -> bool:
    """Remove an adapter from the promoted sidecar. The model id remains
    in the live registry until this process restarts."""
    promoted = _load_promoted(state)
    if adapter_id not in promoted:
        return False
    promoted = [x for x in promoted if x != adapter_id]
    _save_promoted(state, promoted)
    try:
        from .adapters import get_adapter, write_adapter
        from .models import ADAPTER_STATUS_EVAL_PASSED
        a = get_adapter(state, adapter_id)
        if a is not None and a.promoted_as_model_id:
            updated = a.__class__(
                **{**a.__dict__,
                   "status": ADAPTER_STATUS_EVAL_PASSED,
                   "promoted_as_model_id": None},
            )
            write_adapter(state, updated)
    except Exception:  # noqa: BLE001
        pass
    try:
        metrics.finetuning_promoted_total.set(float(len(_load_promoted(state))))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..plugins import dispatch
        dispatch("finetuning.adapter.unpromoted", adapter_id=adapter_id)
    except Exception:  # noqa: BLE001
        pass
    return True


def re_register_promoted(state: State) -> int:
    """Re-register all promoted adapters with the live AI registry.

    Called by ``ai.models`` at import time so a fresh process can still
    route to previously-promoted adapters.
    """
    n = 0
    for aid in _load_promoted(state):
        from .adapters import get_adapter
        a = get_adapter(state, aid)
        if a is None:
            continue
        # status flipped by unpromote would be EVAL_PASSED again; only
        # re-register those still showing PROMOTED in state
        if a.status != "promoted":
            continue
        try:
            promote_adapter(state, aid)
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


__all__ = ["promote_adapter", "unpromote_adapter", "list_promoted",
           "is_promoted", "re_register_promoted"]
