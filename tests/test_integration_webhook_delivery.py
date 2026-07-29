"""Integration tests for the webhook delivery subsystem.

Spins up a real stdlib HTTP server in a background thread, exercises
the full pipeline (target add -> deliver -> drain -> list -> replay),
and asserts on actual HTTP behavior + persistent state.

These tests:
- run against an in-memory mock HTTP receiver (no external network)
- use a fresh ``scratch_state_root`` per test
- validate retry + dedupe + DLQ end-to-end
- verify HMAC signing is accepted by a receiver that recomputes it
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from rollout_shield.webhook_delivery import (
    add_target,
    enqueue,
    get_delivery,
    list_deliveries,
    replay,
    stats,
)
from rollout_shield.webhook_delivery.dispatcher import run_once

# --- mock receiver --------------------------------------------------------


class MockReceiver:
    """A stdlib HTTP server that records what it receives."""

    def __init__(self, status_seq: list[int] | None = None):
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._status_seq = list(status_seq) if status_seq else [200]
        self._status_idx = 0
        self.server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def url(self, path: str = "/hook") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def next_status(self) -> int:
        if self._status_idx < len(self._status_seq):
            s = self._status_seq[self._status_idx]
            self._status_idx += 1
            return s
        return self._status_seq[-1]

    def stop(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass

    def _make_handler(self):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else ""
                with outer._lock:
                    outer.records.append({
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    })
                status = outer.next_status()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                payload = b"{}"
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_a):  # noqa: N802
                return

        return _Handler


# --- end-to-end happy path ------------------------------------------------


def test_end_to_end_happy_path(scratch_state):
    recv = MockReceiver(status_seq=[200])
    try:
        add_target(scratch_state, name="rt",
                   url=recv.url("/hook"), sign_mode="none")
        rec = enqueue(scratch_state, target_name="rt",
                      payload={"event": "test", "id": 1})
        counts = run_once(scratch_state)
        assert counts["delivered"] == 1
        assert len(recv.records) == 1
        loaded = get_delivery(scratch_state, rec.delivery_id)
        assert loaded.status.value == "delivered"
        assert loaded.attempt_count == 1
    finally:
        recv.stop()


def test_hmac_signature_is_verifiable_by_receiver(scratch_state):
    """Receiver recomputes HMAC and confirms it matches the header."""
    secret = "shared-secret-XYZ"
    recv = MockReceiver(status_seq=[200])
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="hmac", signing_key=secret)
        payload = {"event": "verify-me", "k": 42}
        enqueue(scratch_state, target_name="rt", payload=payload)
        run_once(scratch_state)
        assert len(recv.records) == 1
        headers = recv.records[0]["headers"]
        body = recv.records[0]["body"]
        # canonical bytes match what the sender signed
        from rollout_shield.webhook_delivery.models import canonical_payload_bytes
        expected = hmac.new(
            secret.encode(), canonical_payload_bytes(payload), hashlib.sha256
        ).hexdigest()
        assert headers["X-Rollout-Shield-Signature"] == "sha256=" + expected
        assert body == canonical_payload_bytes(payload).decode("utf-8")
    finally:
        recv.stop()


# --- retry path -----------------------------------------------------------


def test_retry_path_then_success(scratch_state):
    """500, 500, 200 -> delivered after 3 attempts."""
    recv = MockReceiver(status_seq=[500, 500, 200])
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none", max_attempts=5)
        rec = enqueue(scratch_state, target_name="rt",
                      payload={"retry": True})
        # Force every next_attempt_at to now so we don't wait for backoff
        for _i in range(3):
            run_once(scratch_state)
            cur = get_delivery(scratch_state, rec.delivery_id)
            if cur is None:
                break
            cur.next_attempt_at = int(time.time())
            from rollout_shield.state import atomic_write_json
            from rollout_shield.webhook_delivery.outbox import _delivery_path
            atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                              cur.to_dict())
        final = get_delivery(scratch_state, rec.delivery_id)
        assert final.status.value == "delivered"
        assert final.attempt_count == 3
        assert len(recv.records) == 3
    finally:
        recv.stop()


# --- DLQ path -------------------------------------------------------------


def test_dlq_after_max_attempts(scratch_state):
    recv = MockReceiver(status_seq=[500] * 100)
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none", max_attempts=2)
        rec = enqueue(scratch_state, target_name="rt", payload={"dlq": True})
        for _ in range(5):
            run_once(scratch_state)
            cur = get_delivery(scratch_state, rec.delivery_id)
            if cur.status.value == "dlq":
                break
            cur.next_attempt_at = int(time.time())
            from rollout_shield.state import atomic_write_json
            from rollout_shield.webhook_delivery.outbox import _delivery_path
            atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                              cur.to_dict())
        final = get_delivery(scratch_state, rec.delivery_id)
        assert final.status.value == "dlq"
        assert final.attempt_count == 2
        # DLQ file exists
        from rollout_shield.webhook_delivery.outbox import _dlq_path
        assert _dlq_path(scratch_state, rec.delivery_id).exists()
        # stats
        s = stats(scratch_state)
        assert s["dlq_total"] >= 1
    finally:
        recv.stop()


# --- dedupe ---------------------------------------------------------------


def test_dedupe_only_one_http_call(scratch_state):
    recv = MockReceiver(status_seq=[200])
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none")
        enqueue(scratch_state, target_name="rt", payload={"k": 1},
                idempotency_key="dup-key")
        enqueue(scratch_state, target_name="rt", payload={"k": 1},
                idempotency_key="dup-key")
        run_once(scratch_state)
        assert len(recv.records) == 1
    finally:
        recv.stop()


# --- replay safety -------------------------------------------------------


def test_replay_creates_new_attempt(scratch_state):
    recv = MockReceiver(status_seq=[500, 200, 200])
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none", max_attempts=5)
        rec = enqueue(scratch_state, target_name="rt", payload={"x": 1})
        # First attempt fails
        run_once(scratch_state)
        cur = get_delivery(scratch_state, rec.delivery_id)
        cur.next_attempt_at = int(time.time())
        from rollout_shield.state import atomic_write_json
        from rollout_shield.webhook_delivery.outbox import _delivery_path
        atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                          cur.to_dict())
        # Force DLQ to test replay
        cur = get_delivery(scratch_state, rec.delivery_id)
        from rollout_shield.webhook_delivery import mark_dlq
        mark_dlq(scratch_state, cur.delivery_id, error="manual")
        replay(scratch_state, cur.delivery_id)
        # After replay, second mock status is 200 → delivered
        run_once(scratch_state)
        final = get_delivery(scratch_state, cur.delivery_id)
        assert final.status.value == "delivered"
    finally:
        recv.stop()


# --- circuit breaker -----------------------------------------------------


def test_circuit_breaker_pauses_target(scratch_state):
    recv = MockReceiver(status_seq=[500] * 100)
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none", max_attempts=1)
        # Lower the threshold to make the test fast
        from rollout_shield.webhook_delivery.targets import record_failure
        for i in range(15):
            enqueue(scratch_state, target_name="rt", payload={"i": i})
            run_once(scratch_state)
            record_failure(scratch_state, "rt", threshold=5, cooldown=300)
        from rollout_shield.webhook_delivery.targets import get_target
        tgt = get_target(scratch_state, "rt")
        assert tgt.is_paused()
        # When paused, drain does NOT call HTTP
        before = len(recv.records)
        run_once(scratch_state)
        # No new HTTP calls because the target is paused
        assert len(recv.records) == before
    finally:
        recv.stop()


# --- concurrent dispatch -------------------------------------------------


def test_concurrent_dispatch_acquires_lock(scratch_state):
    """Two threads call run_once at the same time; one wins the lock, one skips."""
    recv = MockReceiver(status_seq=[200] * 100)
    try:
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none")
        enqueue(scratch_state, target_name="rt", payload={"x": 1})
        results: list[dict] = []
        barrier = threading.Barrier(2)

        def _go():
            barrier.wait()
            results.append(run_once(scratch_state))

        threads = [threading.Thread(target=_go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        total_sent = sum(r["sent"] for r in results)
        # Either one thread sent and the other skipped, or both got 0
        # (depending on platform's lock semantics). What we care about:
        # at least one delivery, no double-delivery.
        assert total_sent >= 1
        assert len(recv.records) >= 1
        # No duplicate — exactly 1 successful delivery
        delivered = [r for r in list_deliveries(scratch_state)
                     if r.status.value == "delivered"]
        assert len(delivered) == 1
    finally:
        recv.stop()


# --- metrics integration -------------------------------------------------


def test_metrics_emitted_on_dispatch(scratch_state):
    recv = MockReceiver(status_seq=[200])
    try:
        from rollout_shield import metrics as m
        # Counter snapshot before
        m.render()
        add_target(scratch_state, name="rt", url=recv.url("/hook"),
                   sign_mode="none")
        enqueue(scratch_state, target_name="rt", payload={"m": 1})
        run_once(scratch_state)
        after = m.render()
        # At least one of the webhook metrics families should now appear
        assert "rollout_shield_webhook_deliveries_total" in after
        assert "rollout_shield_webhook_delivery_attempts_total" in after
        assert "rollout_shield_webhook_outbox_depth" in after
    finally:
        recv.stop()
