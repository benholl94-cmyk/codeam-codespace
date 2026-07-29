"""Webhook delivery subsystem for rollout-shield.

A production-grade outbound webhook delivery pipeline built around the
**outbox pattern**. Every delivery intent is persisted atomically
before any HTTP attempt, then delivered with retry / exponential
backoff / deduplication / HMAC signing. Delivery status is durable
and operators can replay, inspect, and reason about every event.

Public API
----------

::

    from rollout_shield.webhook_delivery import (
        enqueue, list_deliveries, get_delivery, replay,
        add_target, remove_target, list_targets, stats, run_dispatcher,
    )

Targets are persistent. Deliveries are persistent. The dispatcher can
run as a one-shot ("drain") or as a long-lived background worker.

State layout under <state_root>/webhooks/::

    targets/<name>.json           # delivery target configuration
    deliveries/<delivery-id>.json # current snapshot (atomic-write)
    outbox/<YYYY-MM-DD>.jsonl     # append-only event log
    dlq/<delivery-id>.json        # dead-letter
    stats.json                    # rolled counters
    .lock                         # advisory cross-process lockfile

Module entry points
-------------------

- CLI: ``rollout-shield webhooks ...`` (registered in ``cli.py``)
- HTTP: ``/api/webhooks/...`` (registered in ``http_server.py``)
- Daemon: ``run_dispatcher()`` is invoked from ``monitor_daemon.run_once``
  on every cycle when ``config.webhooks_enabled`` is true.
"""

from __future__ import annotations

from .dedupe import coalesce
from .dispatcher import run_dispatcher, run_once
from .models import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_DEDUPE_WINDOW_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    DeliveryRecord,
    DeliveryStatus,
    DeliveryTarget,
    SignMode,
    canonical_payload_bytes,
    new_delivery_id,
    new_trace_id,
    payload_sha256,
)
from .outbox import (
    OutboxError,
    enqueue,
    get_delivery,
    iter_deliveries,
    list_deliveries,
    mark_dlq,
    mark_status,
    stats,
)
from .replay import replay, replay_all
from .targets import (
    TargetError,
    add_target,
    get_target,
    list_targets,
    record_failure,
    record_success,
    remove_target,
)

__all__ = [
    # models
    "DeliveryRecord",
    "DeliveryStatus",
    "DeliveryTarget",
    "SignMode",
    "DEFAULT_BACKOFF_SECONDS",
    "DEFAULT_DEDUPE_WINDOW_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "canonical_payload_bytes",
    "new_delivery_id",
    "new_trace_id",
    "payload_sha256",
    # outbox
    "enqueue",
    "get_delivery",
    "iter_deliveries",
    "list_deliveries",
    "mark_dlq",
    "mark_status",
    "stats",
    "OutboxError",
    # dedupe
    "coalesce",
    # targets
    "add_target",
    "get_target",
    "list_targets",
    "remove_target",
    "record_failure",
    "record_success",
    "TargetError",
    # replay
    "replay",
    "replay_all",
    # dispatcher
    "run_dispatcher",
    "run_once",
]
