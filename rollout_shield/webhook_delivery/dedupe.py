"""Per-target deduplication for webhook deliveries.

A delivery is a duplicate of an existing one when all three match:

- ``target_name``
- ``idempotency_key`` (the caller's correlation key, or auto-generated)
- ``payload_sha256``   (the SHA-256 of the canonical payload)

…AND the existing record's ``updated_at`` is within the target's
``dedupe_window_seconds`` (default 300s = 5 min).

Coalescing is intentionally lightweight: a single linear scan over
``deliveries/`` and ``dlq/``. The outbox is small (typically < 1k
active records per node); for higher volumes, replace this with an
indexed dedupe store.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from ..state import State
from .models import DeliveryRecord


def _recent(state: State) -> Iterable[DeliveryRecord]:
    from .outbox import _deliveries_dir, _dlq_dir
    candidates = list(_deliveries_dir(state).glob("*.json"))
    candidates.extend(_dlq_dir(state).glob("*.json"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    import json
    for path in candidates:
        try:
            yield DeliveryRecord.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue


def coalesce(state: State, target_name: str,
             idempotency_key: str, payload_sha256: str) -> DeliveryRecord | None:
    """Return an existing recent matching delivery, or None.

    Honors the target's ``dedupe_window_seconds`` if the target is
    registered. Default window = 300s.
    """
    from .targets import get_target

    window = 300
    tgt = get_target(state, target_name)
    if tgt is not None:
        window = int(tgt.dedupe_window_seconds)

    cutoff = int(time.time()) - window
    for rec in _recent(state):
        if rec.target_name != target_name:
            continue
        if rec.idempotency_key != idempotency_key:
            continue
        if rec.payload_sha256 != payload_sha256:
            continue
        if rec.updated_at < cutoff:
            continue
        return rec
    return None
