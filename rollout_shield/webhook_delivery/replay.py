"""Manual + automatic replay for failed deliveries.

Replay semantics:
- A replay creates a NEW attempt on the EXISTING delivery record;
  it does NOT create a new delivery_id.
- The existing record's ``status`` is reset to ``pending`` and its
  ``attempt_count`` is preserved (cumulative, not reset).
- The replay count is incremented so operators can distinguish
  original attempts from operator-driven replays.

Operators can replay a single delivery (``replay(delivery_id)``)
or all deliveries in a given status (``replay_all(status)``).
"""

from __future__ import annotations

import time

from ..state import State, atomic_write_json
from .models import DeliveryRecord, DeliveryStatus
from .outbox import (
    _bump_stats,
    _delivery_path,
    _emit_event,
    get_delivery,
    iter_deliveries,
)


class ReplayError(Exception):
    """Raised when replay cannot proceed."""


def replay(state: State, delivery_id: str) -> DeliveryRecord:
    """Reset a delivery to pending; next dispatcher tick will retry."""
    rec = get_delivery(state, delivery_id)
    if rec is None:
        raise ReplayError(f"unknown delivery_id: {delivery_id!r}")
    if rec.status == DeliveryStatus.DELIVERED:
        raise ReplayError(
            f"delivery {delivery_id!r} already delivered; nothing to replay"
        )
    rec.status = DeliveryStatus.PENDING
    rec.next_attempt_at = int(time.time())
    rec.last_error = ""
    rec.updated_at = int(time.time())
    # keep attempts[] history; do not reset attempt_count
    atomic_write_json(_delivery_path(state, delivery_id), rec.to_dict())
    _emit_event(state, delivery_id, "replayed")
    _bump_stats(state, replayed_total=1)
    return rec


def replay_all(state: State, status: DeliveryStatus | str) -> list[str]:
    """Replay every delivery in the given status. Returns the list of replayed ids."""
    wanted = (
        status if isinstance(status, DeliveryStatus)
        else DeliveryStatus(status)
    )
    replayed: list[str] = []
    for rec in iter_deliveries(state, status=wanted):
        try:
            replay(state, rec.delivery_id)
            replayed.append(rec.delivery_id)
        except ReplayError:
            continue
    return replayed
