"""Baseline output — fall back to the registered base model.

The base model runs through ``ai.models.get_model`` when it is
registered, else the simplest possible deterministic string for unknown
ids (so eval still produces numbers in tests / stdlib-only envs).
"""
from __future__ import annotations

import hashlib
from typing import Any


def baseline_generate(state: Any, model_id: str, prompt: str) -> str:
    try:
        from ..ai.models import get_model
        info = get_model(model_id)
        if info is not None:
            try:
                res = info.fn(prompt, {})
                return (res or {}).get("text", "") or ""
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # unknown base — hash-derived stable string so baseline is reproducible
    h = hashlib.sha256((model_id + "|" + prompt).encode()).hexdigest()[:16]
    return f"[{model_id} baseline:{h}]"


__all__ = ["baseline_generate"]
