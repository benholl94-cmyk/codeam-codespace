"""Outbound webhook dispatcher.

Responsibilities:

- Drain the outbox: find deliveries whose ``next_attempt_at`` has
  passed and whose target exists + is enabled + not paused.
- Send each delivery over HTTP (stdlib ``urllib.request`` — no
  third-party dependencies).
- Apply retry policy: 5xx + 408 + 429 → retry with exponential
  backoff; 4xx (other) → fail without retry; 2xx → delivered.
- After ``max_attempts`` failures → move to DLQ.
- Emit metrics and structured logs.
- Use an advisory cross-process lockfile so two dispatchers do not
  double-send.

Two execution modes:

- ``run_once(state)`` — drain everything currently pending, then
  return. Suitable for one-shot invocation from the monitor daemon
  cycle or from a CLI ``webhooks drain`` command.
- ``run_dispatcher(state, interval_seconds=2)`` — loop forever,
  sleeping ``interval_seconds`` between drains. Suitable for a
  dedicated worker.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import metrics
from ..logging import get_logger
from ..state import State, atomic_write_json
from .models import (
    DEFAULT_BACKOFF_SECONDS,
    DeliveryRecord,
    DeliveryStatus,
    DeliveryTarget,
)
from .outbox import (
    _delivery_path,
    _emit_event,
    iter_deliveries,
    mark_dlq,
    mark_status,
)
from .signer import build_headers
from .targets import (
    get_target,
)
from .targets import (
    record_failure as target_record_failure,
)
from .targets import (
    record_success as target_record_success,
)

LOG = get_logger(__name__)


# --- platform-specific file locking ----------------------------------------

def _try_lock(fd: int) -> bool:
    """Try to acquire an exclusive lock; return True on success.

    Falls back to a no-op if the platform doesn't support ``flock``.
    """
    try:
        import fcntl  # POSIX
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (ImportError, OSError, BlockingIOError):
        pass
    try:
        import msvcrt  # Windows
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except (ImportError, OSError):
        return False


def _lockfile(state: State) -> Path:
    d = state.root / "webhooks"
    d.mkdir(parents=True, exist_ok=True)
    return d / ".lock"


# --- HTTP delivery ----------------------------------------------------------


class HttpResult:
    """Result of one HTTP delivery attempt."""

    __slots__ = ("status", "error", "duration_ms")

    def __init__(self, status: int = 0, error: str = "", duration_ms: int = 0):
        self.status = status
        self.error = error
        self.duration_ms = duration_ms


def _http_post(url: str, payload_bytes: bytes, headers: dict[str, str],
               timeout: float) -> HttpResult:
    """Send a single HTTP POST. Returns an HttpResult."""
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url, data=payload_bytes, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            duration_ms = int((time.monotonic() - started) * 1000)
            return HttpResult(status=resp.status, duration_ms=duration_ms)
    except urllib.error.HTTPError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return HttpResult(status=exc.code, error=str(exc), duration_ms=duration_ms)
    except urllib.error.URLError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return HttpResult(status=0, error=f"URLError: {exc.reason}", duration_ms=duration_ms)
    except (TimeoutError, OSError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return HttpResult(status=0, error=f"{type(exc).__name__}: {exc}", duration_ms=duration_ms)
    except Exception as exc:  # noqa: BLE001 — never let a delivery kill the daemon
        duration_ms = int((time.monotonic() - started) * 1000)
        return HttpResult(status=0, error=f"{type(exc).__name__}: {exc}", duration_ms=duration_ms)


# --- attempt decision -------------------------------------------------------


def _is_retryable(result: HttpResult) -> bool:
    """Return True if the result is a transient failure worth retrying."""
    if result.status == 0:
        return True  # network/timeout
    if result.status in (408, 425, 429):
        return True
    if 500 <= result.status < 600:
        return True
    return False


def _compute_next_attempt(attempt_count: int, schedule: tuple[int, ...]) -> int:
    """Return Unix ts of the next attempt given current attempt count."""
    if attempt_count <= 0:
        return int(time.time())
    idx = min(attempt_count - 1, len(schedule) - 1)
    delay = schedule[idx]
    return int(time.time()) + delay


# --- one delivery -----------------------------------------------------------


def _attempt_delivery(state: State, rec: DeliveryRecord,
                      target: DeliveryTarget) -> HttpResult:
    """Send one HTTP attempt for a delivery. Mutates rec.attempts."""
    from .models import AttemptRecord

    payload_bytes = json.dumps(
        rec.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    headers = build_headers(target, rec.payload, state=state)
    rec.headers = headers  # cache for debugging

    result = _http_post(target.url, payload_bytes, headers,
                        timeout=target.timeout_seconds)

    rec.attempt_count += 1
    attempt = AttemptRecord(
        attempt_no=rec.attempt_count,
        started_at=int(time.time()) - int(result.duration_ms / 1000),
        finished_at=int(time.time()),
        http_status=result.status,
        error=result.error,
        duration_ms=result.duration_ms,
    )
    rec.attempts.append(attempt)
    return result


def _process_one(state: State, rec: DeliveryRecord) -> DeliveryRecord | None:
    """Process a single delivery. Returns the updated record, or None if skipped."""
    target = get_target(state, rec.target_name)
    if target is None:
        rec = mark_status(state, rec.delivery_id, DeliveryStatus.FAILED,
                          last_error=f"target {rec.target_name!r} not found")
        return rec
    if not target.enabled:
        # leave it pending; operator disabled the target — do nothing
        return None
    if target.is_paused():
        # circuit breaker is open — push next_attempt_at past the pause
        rec.next_attempt_at = max(rec.next_attempt_at, target.paused_until)
        rec.updated_at = int(time.time())
        atomic_write_json(_delivery_path(state, rec.delivery_id), rec.to_dict())
        return rec

    # dispatch
    mark_status(state, rec.delivery_id, DeliveryStatus.IN_FLIGHT)
    result = _attempt_delivery(state, rec, target)
    # Persist the updated record (with the new attempt appended) so
    # later status reads see the correct attempt_count.
    atomic_write_json(_delivery_path(state, rec.delivery_id), rec.to_dict())
    duration_s = max(result.duration_ms / 1000.0, 0.0)

    metrics.webhook_delivery_attempts_total.inc(
        labels=(rec.target_name, _result_label(result)))
    metrics.webhook_delivery_duration_seconds.observe(
        duration_s, labels=(rec.target_name,))

    LOG.info(
        "webhook delivery attempt",
        extra={
            "delivery_id": rec.delivery_id,
            "target": rec.target_name,
            "attempt_no": rec.attempt_count,
            "http_status": result.status,
            "duration_ms": result.duration_ms,
            "trace_id": rec.trace_id,
        },
    )

    if 200 <= result.status < 300:
        rec.status = DeliveryStatus.DELIVERED
        rec.last_error = ""
        target_record_success(state, rec.target_name)
        mark_status(state, rec.delivery_id, DeliveryStatus.DELIVERED)
        metrics.webhook_deliveries_total.inc(
            labels=(rec.target_name, "delivered"))
        # dispatch plugin hook (best-effort)
        _emit_webhook_delivered_hook(rec, target, result)
        return rec

    # failure path
    retryable = _is_retryable(result)
    if not retryable:
        # permanent failure — straight to DLQ
        target_record_failure(state, rec.target_name)
        mark_dlq(state, rec.delivery_id,
                 error=f"non-retryable HTTP {result.status}: {result.error}")
        metrics.webhook_deliveries_total.inc(
            labels=(rec.target_name, "dlq"))
        _emit_event(state, rec.delivery_id, "delivery_failed_permanent",
                    http_status=result.status, error=result.error)
        return rec

    # retryable
    target_record_failure(state, rec.target_name)
    if rec.attempt_count >= target.max_attempts:
        mark_dlq(state, rec.delivery_id,
                 error=f"max attempts ({target.max_attempts}) exhausted; "
                       f"last HTTP {result.status}: {result.error}")
        metrics.webhook_deliveries_total.inc(
            labels=(rec.target_name, "dlq"))
        return rec

    rec.status = DeliveryStatus.FAILED
    rec.last_error = f"HTTP {result.status}: {result.error}"
    rec.next_attempt_at = _compute_next_attempt(rec.attempt_count, DEFAULT_BACKOFF_SECONDS)
    mark_status(state, rec.delivery_id, DeliveryStatus.FAILED,
                last_error=rec.last_error)
    _emit_event(state, rec.delivery_id, "scheduled_retry",
                next_attempt_at=rec.next_attempt_at,
                http_status=result.status)
    return rec


def _result_label(result: HttpResult) -> str:
    if 200 <= result.status < 300:
        return "success"
    if result.status == 0:
        return "network_error"
    if 400 <= result.status < 500:
        return "client_error"
    if 500 <= result.status < 600:
        return "server_error"
    return "unknown"


def _emit_webhook_delivered_hook(rec: DeliveryRecord, target: DeliveryTarget,
                                 result: HttpResult) -> None:
    """Best-effort dispatch of the ``webhook.delivered`` plugin hook."""
    try:
        from ..plugins import dispatch
    except ImportError:
        return
    try:
        dispatch("webhook.delivered", {
            "delivery_id": rec.delivery_id,
            "target": target.name,
            "url": target.url,
            "http_status": result.status,
            "duration_ms": result.duration_ms,
            "trace_id": rec.trace_id,
        })
    except Exception:  # noqa: BLE001 — plugin errors must never break delivery
        LOG.warning("plugin hook webhook.delivered raised", exc_info=True)


# --- driver -----------------------------------------------------------------


def _drain_pending(state: State) -> dict[str, int]:
    """Process all currently-due deliveries. Returns counts.

    Picks up both ``PENDING`` records (never attempted yet) and
    ``FAILED`` records whose ``next_attempt_at`` has passed
    (scheduled for retry).
    """
    counts = {"sent": 0, "delivered": 0, "failed": 0, "dlq": 0, "skipped": 0}
    now = int(time.time())
    due: list[DeliveryRecord] = []
    for status in (DeliveryStatus.PENDING, DeliveryStatus.FAILED):
        for rec in iter_deliveries(state, status=status):
            if rec.next_attempt_at <= now:
                due.append(rec)
    for rec in due:
        out = _process_one(state, rec)
        if out is None:
            counts["skipped"] += 1
            continue
        counts["sent"] += 1
        if out.status == DeliveryStatus.DELIVERED:
            counts["delivered"] += 1
        elif out.status == DeliveryStatus.DLQ:
            counts["dlq"] += 1
        else:
            counts["failed"] += 1
    # update gauges
    from .outbox import stats as outbox_stats
    s = outbox_stats(state)
    metrics.webhook_outbox_depth.set(s.get("outbox_depth", 0))
    metrics.webhook_dlq_depth.set(s.get("dlq_depth", 0))
    metrics.webhook_targets_count.set(s.get("targets_count", 0))
    return counts


def run_once(state: State) -> dict[str, int]:
    """Drain the outbox once. Acquires advisory lock; if held, returns empty counts.

    Returns a counts dict: ``{sent, delivered, failed, dlq, skipped}``.
    """
    lockpath = _lockfile(state)
    try:
        fd = os.open(str(lockpath), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        LOG.warning("could not open webhook lockfile: %s", exc)
        return {"sent": 0, "delivered": 0, "failed": 0, "dlq": 0, "skipped": 0}
    try:
        if not _try_lock(fd):
            LOG.debug("webhook dispatcher: another process holds the lock; skipping")
            return {"sent": 0, "delivered": 0, "failed": 0, "dlq": 0, "skipped": 0}
        return _drain_pending(state)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def run_dispatcher(state: State, interval_seconds: float = 2.0,
                   stop_after: int | None = None) -> dict[str, int]:
    """Loop forever, draining every ``interval_seconds``. SIGINT-safe.

    ``stop_after`` is for tests — exit after N drains.
    """
    LOG.info("webhook dispatcher started", extra={"interval_seconds": interval_seconds})
    total = {"sent": 0, "delivered": 0, "failed": 0, "dlq": 0, "skipped": 0}
    drains = 0
    try:
        while True:
            counts = run_once(state)
            for k, v in counts.items():
                total[k] += v
            drains += 1
            if stop_after is not None and drains >= stop_after:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        LOG.info("webhook dispatcher interrupted", extra={"drains": drains})
    return total
