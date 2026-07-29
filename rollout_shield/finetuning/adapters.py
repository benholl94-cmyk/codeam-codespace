"""Adapter manifest store.

Each adapter is a JSON file at
``<state>/finetuning/adapters/<adapter_id>.json``. Loaded on demand and
written via ``State.atomic_write_json`` so the state can never be torn.

This module does NOT call into the AI layer — it only persists records.
Promotion happens in ``promote.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..state import State, atomic_write_json
from .models import AdapterRecord


def adapter_path(state: State, adapter_id: str) -> Path:
    return state.finetuning_adapters_dir / f"{adapter_id}.json"


def write_adapter(state: State, rec: AdapterRecord) -> None:
    atomic_write_json(adapter_path(state, rec.adapter_id), rec.to_dict())


def get_adapter(state: State, adapter_id: str) -> AdapterRecord | None:
    p = adapter_path(state, adapter_id)
    if not p.exists():
        return None
    try:
        return AdapterRecord.from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def list_adapters(state: State,
                  status: str | None = None,
                  backend: str | None = None) -> list[AdapterRecord]:
    out: list[AdapterRecord] = []
    for p in sorted(state.finetuning_adapters_dir.glob("*.json")):
        try:
            rec = AdapterRecord.from_dict(json.loads(p.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if status and rec.status != status:
            continue
        if backend and rec.backend != backend:
            continue
        out.append(rec)
    return out


def remove_adapter(state: State, adapter_id: str) -> bool:
    p = adapter_path(state, adapter_id)
    if not p.exists():
        return False
    p.unlink()
    return True
