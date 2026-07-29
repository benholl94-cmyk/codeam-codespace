"""Durable outbox for webhook deliveries.

Each delivery is persisted as a single JSON file under
``<state_root>/webhooks/deliveries/<delivery-id>.json``. Writes go
through ``state.atomic_write_json`` so a crash mid-write cannot
corrupt the state.

An append-only event log under
``<state_root>/webhooks/outbox/<YYYY-MM-DD>.jsonl`` records every
state transition (enqueued / status-changed / dlq / replayed). The
event log is for audit; the canonical current state lives in the
JSON snapshot.

Public surface:
- ``enqueue``      — append a delivery to the outbox
- ``get_delivery`` — load current state by id
- ``list_deliveries`` / ``iter_deliveries`` — scan with filters
- ``mark_status``  — atomic status transition with event-log emit
- ``mark_dlq``     — convenience wrapper for DLQ + log
- ``stats``        — rolled counters from ``stats.json``
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state import State, atomic_append_jsonl, atomic_write_json
from .dedupe import coalesce
from .models import (
    DeliveryRecord,
    DeliveryStatus,
    new_delivery_id,
    new_trace_id,
    payload_sha256,
)


class OutboxError(Exception):
    """Raised when an outbox operation fails."""


def _webhooks_root(state: State) -> Path:
    d = state.root / "webhooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _deliveries_dir(state: State) -> Path:
    d = _webhooks_root(state) / "deliveries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dlq_dir(state: State) -> Path:
    d = _webhooks_root(state) / "dlq"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _outbox_dir(state: State) -> Path:
    d = _webhooks_root(state) / "outbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stats_path(state: State) -> Path:
    return _webhooks_root(state) / "stats.json"


def _delivery_path(state: State, delivery_id: str) -> Path:
    if "/" in delivery_id or ".." in delivery_id:
        raise OutboxError(f"invalid delivery_id: {delivery_id!r}")
    return _deliveries_dir(state) / f"{delivery_id}.json"


def _dlq_path(state: State, delivery_id: str) -> Path:
    return _dlq_dir(state) / f"{delivery_id}.json"


def _outbox_path(state: State, ts: int | None = None) -> Path:
    dt = datetime.fromtimestamp(ts or int(time.time()), tz=timezone.utc)
    return _outbox_dir(state) / f"{dt.strftime('%Y-%m-%d')}.jsonl"


def _emit_event(state: State, delivery_id: str, event: str, **fields: Any) -> None:
    """Append an event row to the daily outbox log."""
    rec = {
        "ts": int(time.time()),
        "delivery_id": delivery_id,
        "event": event,
        **fields,
    }
    atomic_append_jsonl(_outbox_path(state, rec["ts"]), rec)


def _load_stats(state: State) -> dict[str, int]:
    p = _stats_path(state)
    if not p.exists():
        return {
            "enqueued_total": 0,
            "delivered_total": 0,
            "failed_total": 0,
            "dlq_total": 0,
            "replayed_total": 0,
        }
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {k: 0 for k in (
            "enqueued_total", "delivered_total", "failed_total",
            "dlq_total", "replayed_total",
        )}


def _bump_stats(state: State, **fields: int) -> None:
    s = _load_stats(state)
    for k, v in fields.items():
        s[k] = s.get(k, 0) + int(v)
    s["updated_at"] = int(time.time())
    atomic_write_json(_stats_path(state), s)


# --- public API ------------------------------------------------------------


def enqueue(state: State, target_name: str, payload: dict[str, Any],
            idempotency_key: str | None = None,
            trace_id: str | None = None) -> DeliveryRecord:
    """Persist a new delivery record.

    Honors per-target dedupe: if an existing delivery for the same
    (target, idempotency_key, payload_sha256) is still pending or
    recently delivered, returns the existing record instead of
    creating a new one.
    """
    if not target_name:
        raise OutboxError("target_name is required")
    if not isinstance(payload, dict):
        raise OutboxError("payload must be a JSON object")
    p_hash = payload_sha256(payload)
    idem = idempotency_key or "auto-" + uuid.uuid4().hex[:12]

    existing = coalesce(state, target_name=target_name,
                        idempotency_key=idem, payload_sha256=p_hash)
    if existing is not None:
        return existing

    rec = DeliveryRecord(
        delivery_id=new_delivery_id(),
        target_name=target_name,
        payload=payload,
        payload_sha256=p_hash,
        idempotency_key=idem,
        trace_id=trace_id or new_trace_id(),
    )
    path = _delivery_path(state, rec.delivery_id)
    atomic_write_json(path, rec.to_dict())
    _emit_event(state, rec.delivery_id, "enqueued",
                target=target_name, payload_sha256=p_hash,
                idempotency_key=idem, trace_id=rec.trace_id)
    _bump_stats(state, enqueued_total=1)
    return rec


def get_delivery(state: State, delivery_id: str) -> DeliveryRecord | None:
    """Load the current snapshot of a delivery."""
    path = _delivery_path(state, delivery_id)
    if not path.exists():
        return None
    try:
        return DeliveryRecord.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def iter_deliveries(state: State,
                    status: DeliveryStatus | str | None = None,
                    target: str | None = None) -> Iterable[DeliveryRecord]:
    """Iterate all known deliveries newest-first.

    Yields records from both ``deliveries/`` and ``dlq/`` so
    historical state is visible. Filtered by status / target as
    requested.
    """
    wanted_status = (
        DeliveryStatus(status).value if isinstance(status, str)
        else status.value if isinstance(status, DeliveryStatus)
        else None
    )
    seen: set[str] = set()
    candidates: list[Path] = []
    candidates.extend(_deliveries_dir(state).glob("*.json"))
    candidates.extend(_dlq_dir(state).glob("*.json"))
    # newest-first by mtime
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        delivery_id = path.stem
        if delivery_id in seen:
            continue
        seen.add(delivery_id)
        try:
            rec = DeliveryRecord.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if wanted_status and rec.status.value != wanted_status:
            continue
        if target and rec.target_name != target:
            continue
        yield rec


def list_deliveries(state: State, status: DeliveryStatus | str | None = None,
                    target: str | None = None,
                    limit: int | None = 100) -> list[DeliveryRecord]:
    """List deliveries (newest-first, optionally filtered)."""
    out: list[DeliveryRecord] = []
    for rec in iter_deliveries(state, status=status, target=target):
        out.append(rec)
        if limit is not None and len(out) >= limit:
            break
    return out


def mark_status(state: State, delivery_id: str,
                new_status: DeliveryStatus,
                last_error: str = "",
                trace_id: str | None = None) -> DeliveryRecord:
    """Atomically transition a delivery to a new status.

    Persists the snapshot, appends to the event log, and bumps
    stats. Returns the updated record.
    """
    rec = get_delivery(state, delivery_id)
    if rec is None:
        raise OutboxError(f"unknown delivery_id: {delivery_id!r}")
    previous = rec.status
    rec.status = new_status
    rec.last_error = last_error
    rec.updated_at = int(time.time())
    if trace_id:
        rec.trace_id = trace_id
    atomic_write_json(_delivery_path(state, delivery_id), rec.to_dict())
    _emit_event(state, delivery_id, "status_changed",
                from_status=previous.value, to_status=new_status.value,
                last_error=last_error)
    if new_status == DeliveryStatus.DELIVERED:
        _bump_stats(state, delivered_total=1)
    elif new_status == DeliveryStatus.FAILED:
        _bump_stats(state, failed_total=1)
    elif new_status == DeliveryStatus.DLQ:
        _bump_stats(state, dlq_total=1)
    return rec


def mark_dlq(state: State, delivery_id: str, error: str) -> DeliveryRecord:
    """Move a delivery to the dead-letter directory."""
    rec = mark_status(state, delivery_id, DeliveryStatus.DLQ, last_error=error)
    # Copy to dlq/ for visibility
    atomic_write_json(_dlq_path(state, delivery_id), rec.to_dict())
    _emit_event(state, delivery_id, "dlq_moved", error=error)
    return rec


def stats(state: State) -> dict[str, Any]:
    """Return rolled counters and live outbox depth."""
    base = _load_stats(state)
    base["outbox_depth"] = sum(
        1 for rec in iter_deliveries(state) if rec.status == DeliveryStatus.PENDING
    )
    base["dlq_depth"] = sum(
        1 for _ in _dlq_dir(state).glob("*.json")
    )
    base["targets_count"] = sum(1 for _ in (_webhooks_root(state) / "targets").glob("*.json"))
    return base
