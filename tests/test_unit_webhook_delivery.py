"""Unit tests for the webhook delivery subsystem.

Coverage:
- models: canonical payload, sha256, new id
- signer: HMAC + Ed25519 headers (Ed25519 skipped if no cryptography)
- targets: add / list / get / remove / circuit breaker
- outbox: enqueue + status transitions + dedupe coalescing
- dedupe: window honored
- dispatcher: backoff schedule + retry decision (mocked HTTP)
- replay: status reset + counter bump
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from rollout_shield.webhook_delivery import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_DEDUPE_WINDOW_SECONDS,
    DeliveryStatus,
    DeliveryTarget,
    SignMode,
    add_target,
    canonical_payload_bytes,
    enqueue,
    get_delivery,
    iter_deliveries,
    list_deliveries,
    list_targets,
    mark_dlq,
    mark_status,
    new_delivery_id,
    payload_sha256,
    remove_target,
    replay,
    replay_all,
    stats,
)
from rollout_shield.webhook_delivery.signer import (
    build_headers,
    sign_payload_hmac,
)

# --- models ----------------------------------------------------------------


def test_canonical_payload_is_stable():
    p1 = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    p2 = {"a": 1, "nested": {"x": 1, "y": 2}, "b": 2}
    assert canonical_payload_bytes(p1) == canonical_payload_bytes(p2)


def test_payload_sha256_format():
    h = payload_sha256({"a": 1})
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_new_delivery_id_unique():
    seen = {new_delivery_id() for _ in range(50)}
    assert len(seen) == 50


# --- signer ----------------------------------------------------------------


def test_hmac_sign_stable():
    payload = b'{"a":1}'
    sig1 = sign_payload_hmac("secret", payload)
    sig2 = sign_payload_hmac("secret", payload)
    assert sig1 == sig2
    assert sig1.startswith("sha256=")
    # sanity vs stdlib
    expected = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    assert sig1 == "sha256=" + expected


def test_hmac_sign_changes_with_secret():
    payload = b'{"a":1}'
    assert sign_payload_hmac("a", payload) != sign_payload_hmac("b", payload)


def test_build_headers_no_sign():
    tgt = DeliveryTarget(name="t", url="http://x", sign_mode=SignMode.NONE)
    h = build_headers(tgt, {"a": 1})
    assert h["Content-Type"] == "application/json"
    assert h["X-Rollout-Shield-Signature"] == "none"
    assert "X-Rollout-Shield-Timestamp" in h


def test_build_headers_hmac():
    tgt = DeliveryTarget(name="t", url="http://x",
                         sign_mode=SignMode.HMAC, signing_key="topsecret")
    h = build_headers(tgt, {"a": 1})
    assert h["X-Rollout-Shield-Signature"].startswith("sha256=")
    assert h["X-Rollout-Shield-Key-Id"]


def test_build_headers_hmac_requires_key():
    tgt = DeliveryTarget(name="t", url="http://x", sign_mode=SignMode.HMAC)
    with pytest.raises(ValueError):
        build_headers(tgt, {"a": 1})


def test_build_headers_ed25519_requires_key(scratch_state):
    tgt = DeliveryTarget(name="t", url="http://x", sign_mode=SignMode.ED25519)
    with pytest.raises(ValueError):
        build_headers(tgt, {"a": 1})


@pytest.mark.usefixtures("requires_cryptography")
def test_build_headers_ed25519_with_real_key(scratch_state):
    """Register a real Ed25519 key and use it to sign."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                  NoEncryption()).decode("ascii")
    pub_pem = priv.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    key_id = "agk_ed25519_test"
    scratch_state.put_key(key_id, {
        "id": key_id, "agent_id": "test",
        "algorithm": "Ed25519",
        "public_key_pem": pub_pem,
        "fingerprint": "fp",
        "private_key_path": str(scratch_state.root / "keys_material" / f"{key_id}.pem"),
        "created_at": int(time.time()),
    })
    (scratch_state.root / "keys_material").mkdir(parents=True, exist_ok=True)
    (scratch_state.root / "keys_material" / f"{key_id}.pem").write_text(priv_pem)

    tgt = DeliveryTarget(name="t", url="http://x",
                         sign_mode=SignMode.ED25519, signing_key=key_id)
    h = build_headers(tgt, {"a": 1}, state=scratch_state)
    assert h["X-Rollout-Shield-Signature"].startswith("ed25519=")
    assert h["X-Rollout-Shield-Key-Id"] == key_id


# --- targets ---------------------------------------------------------------


def test_target_add_get_list_remove(scratch_state):
    tgt = add_target(scratch_state, name="t1", url="https://example.com/hook")
    assert isinstance(tgt, DeliveryTarget)
    assert tgt.url == "https://example.com/hook"
    loaded = list_targets(scratch_state)
    assert any(t.name == "t1" for t in loaded)
    assert remove_target(scratch_state, "t1") is True
    assert remove_target(scratch_state, "t1") is False


def test_target_validation(scratch_state):
    from rollout_shield.webhook_delivery.targets import TargetError
    with pytest.raises(TargetError):
        add_target(scratch_state, name="", url="https://x")
    with pytest.raises(TargetError):
        add_target(scratch_state, name="t", url="ftp://x")
    with pytest.raises(TargetError):
        add_target(scratch_state, name="t", url="https://x",
                   sign_mode="hmac", signing_key="")


def test_target_circuit_breaker(scratch_state):
    add_target(scratch_state, name="cb", url="https://x/h")
    # 9 failures — should NOT trip
    for _ in range(9):
        tripped = __import__("rollout_shield.webhook_delivery.targets",
                             fromlist=["record_failure"]).record_failure(
            scratch_state, "cb", threshold=10, cooldown=300)
        assert tripped is False
    # 10th failure — SHOULD trip
    tripped = __import__("rollout_shield.webhook_delivery.targets",
                        fromlist=["record_failure"]).record_failure(
        scratch_state, "cb", threshold=10, cooldown=300)
    assert tripped is True
    # is_paused() should now be True
    from rollout_shield.webhook_delivery.targets import get_target
    tgt = get_target(scratch_state, "cb")
    assert tgt.is_paused()
    # record_success resets
    __import__("rollout_shield.webhook_delivery.targets",
               fromlist=["record_success"]).record_success(scratch_state, "cb")
    tgt2 = get_target(scratch_state, "cb")
    assert tgt2.fail_streak == 0
    assert not tgt2.is_paused()


# --- outbox ----------------------------------------------------------------


def test_enqueue_persists(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    assert rec.delivery_id.startswith("del-")
    assert rec.status == DeliveryStatus.PENDING
    loaded = get_delivery(scratch_state, rec.delivery_id)
    assert loaded is not None
    assert loaded.payload == {"k": 1}


def test_enqueue_dedupes_within_window(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    r1 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                 idempotency_key="abc")
    r2 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                 idempotency_key="abc")
    assert r1.delivery_id == r2.delivery_id


def test_enqueue_dedupes_different_keys(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    r1 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                 idempotency_key="abc")
    r2 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                 idempotency_key="xyz")
    assert r1.delivery_id != r2.delivery_id


def test_enqueue_dedupes_different_payloads(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    r1 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                 idempotency_key="abc")
    r2 = enqueue(scratch_state, target_name="t", payload={"k": 2},
                 idempotency_key="abc")
    assert r1.delivery_id != r2.delivery_id


def test_enqueue_validation(scratch_state):
    from rollout_shield.webhook_delivery.outbox import OutboxError
    with pytest.raises(OutboxError):
        enqueue(scratch_state, target_name="", payload={"a": 1})
    with pytest.raises(OutboxError):
        enqueue(scratch_state, target_name="t", payload=[])  # type: ignore[arg-type]


def test_mark_status_atomic(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    updated = mark_status(scratch_state, rec.delivery_id,
                          DeliveryStatus.IN_FLIGHT, last_error="")
    assert updated.status == DeliveryStatus.IN_FLIGHT
    # round-trip
    again = get_delivery(scratch_state, rec.delivery_id)
    assert again.status == DeliveryStatus.IN_FLIGHT


def test_mark_dlq_moves_to_dlq_dir(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    mark_dlq(scratch_state, rec.delivery_id, error="boom")
    dlq_files = list((scratch_state.root / "webhooks" / "dlq").glob("*.json"))
    assert any(f.stem == rec.delivery_id for f in dlq_files)
    # stats now reflects
    s = stats(scratch_state)
    assert s["dlq_total"] >= 1


def test_list_deliveries_filtered(scratch_state):
    add_target(scratch_state, name="t1", url="https://x/h1")
    add_target(scratch_state, name="t2", url="https://x/h2")
    r1 = enqueue(scratch_state, target_name="t1", payload={"x": 1})
    enqueue(scratch_state, target_name="t2", payload={"x": 2})
    only_t1 = list_deliveries(scratch_state, target="t1")
    assert len(only_t1) == 1
    assert only_t1[0].delivery_id == r1.delivery_id
    pending = list_deliveries(scratch_state, status=DeliveryStatus.PENDING)
    assert len(pending) >= 2


def test_iter_deliveries_yields_newest_first(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    recs = [enqueue(scratch_state, target_name="t", payload={"i": i})
            for i in range(5)]
    time.sleep(0.01)  # ensure mtimes are distinct
    recs.append(enqueue(scratch_state, target_name="t", payload={"i": 99}))
    seen = [r.delivery_id for r in iter_deliveries(scratch_state)]
    # last enqueued should be first
    assert seen[0] == recs[-1].delivery_id


# --- dedupe ---------------------------------------------------------------


def test_dedupe_window_expires(scratch_state, monkeypatch):
    """Window should be honored — older records are not deduped against."""
    add_target(scratch_state, name="t", url="https://x/h")
    # First enqueue
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1},
                  idempotency_key="abc")
    # Force the dedupe window to expire by backdating updated_at
    rec.updated_at = int(time.time()) - 10000
    from rollout_shield.state import atomic_write_json
    from rollout_shield.webhook_delivery.outbox import _delivery_path
    atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                      rec.to_dict())
    # New enqueue with same key should now produce a new delivery
    rec2 = enqueue(scratch_state, target_name="t", payload={"k": 1},
                   idempotency_key="abc")
    assert rec.delivery_id != rec2.delivery_id


# --- dispatcher -----------------------------------------------------------


def test_dispatcher_skip_disabled_target(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h", enabled=False)
    enqueue(scratch_state, target_name="t", payload={"k": 1})
    from rollout_shield.webhook_delivery.dispatcher import run_once
    counts = run_once(scratch_state)
    assert counts["sent"] == 0  # skipped because target disabled


def test_dispatcher_missing_target(scratch_state):
    """Target that was removed after enqueue → delivery fails permanently."""
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    remove_target(scratch_state, "t")
    from rollout_shield.webhook_delivery.dispatcher import run_once
    counts = run_once(scratch_state)
    assert counts["sent"] == 1
    assert counts["failed"] == 1
    again = get_delivery(scratch_state, rec.delivery_id)
    assert again.status == DeliveryStatus.FAILED


def test_compute_next_attempt_schedule():
    from rollout_shield.webhook_delivery.dispatcher import _compute_next_attempt
    # 1st attempt -> 0 delay
    assert _compute_next_attempt(0, DEFAULT_BACKOFF_SECONDS) <= int(time.time()) + 1
    # 2nd attempt -> 1s
    assert _compute_next_attempt(1, DEFAULT_BACKOFF_SECONDS) >= int(time.time()) + 0
    # 3rd attempt -> 4s
    assert _compute_next_attempt(2, DEFAULT_BACKOFF_SECONDS) >= int(time.time()) + 3
    # Out-of-range attempts cap at last bucket
    last_delay = DEFAULT_BACKOFF_SECONDS[-1]
    assert _compute_next_attempt(999, DEFAULT_BACKOFF_SECONDS) >= int(time.time()) + last_delay - 1


def test_is_retryable():
    from rollout_shield.webhook_delivery.dispatcher import (
        HttpResult,
        _is_retryable,
    )
    assert _is_retryable(HttpResult(status=0)) is True   # network
    assert _is_retryable(HttpResult(status=408)) is True
    assert _is_retryable(HttpResult(status=429)) is True
    assert _is_retryable(HttpResult(status=503)) is True
    assert _is_retryable(HttpResult(status=200)) is False
    assert _is_retryable(HttpResult(status=400)) is False
    assert _is_retryable(HttpResult(status=403)) is False


def test_dispatcher_dlq_after_max_attempts(scratch_state, monkeypatch):
    """Mock HTTP to always return 500; verify DLQ after max_attempts."""
    add_target(scratch_state, name="t", url="https://x/h",
               max_attempts=3)

    def _fake_post(*_a, **_kw):
        from rollout_shield.webhook_delivery.dispatcher import HttpResult
        return HttpResult(status=503, error="upstream busy", duration_ms=10)

    monkeypatch.setattr(
        "rollout_shield.webhook_delivery.dispatcher._http_post", _fake_post)
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    # First attempt
    from rollout_shield.webhook_delivery.dispatcher import run_once
    run_once(scratch_state)
    # Force next_attempt_at to now so the next attempt is due
    rec_now = get_delivery(scratch_state, rec.delivery_id)
    rec_now.next_attempt_at = int(time.time())
    from rollout_shield.state import atomic_write_json
    from rollout_shield.webhook_delivery.outbox import _delivery_path
    atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                      rec_now.to_dict())
    # Second attempt
    run_once(scratch_state)
    rec_now = get_delivery(scratch_state, rec.delivery_id)
    rec_now.next_attempt_at = int(time.time())
    atomic_write_json(_delivery_path(scratch_state, rec.delivery_id),
                      rec_now.to_dict())
    # Third attempt — should DLQ because max_attempts=3
    run_once(scratch_state)
    final = get_delivery(scratch_state, rec.delivery_id)
    assert final.status == DeliveryStatus.DLQ
    assert final.attempt_count == 3


def test_dispatcher_success_2xx(scratch_state, monkeypatch):
    add_target(scratch_state, name="t", url="https://x/h")

    def _fake_post(*_a, **_kw):
        from rollout_shield.webhook_delivery.dispatcher import HttpResult
        return HttpResult(status=200, duration_ms=5)

    monkeypatch.setattr(
        "rollout_shield.webhook_delivery.dispatcher._http_post", _fake_post)
    enqueue(scratch_state, target_name="t", payload={"k": 1})
    from rollout_shield.webhook_delivery.dispatcher import run_once
    counts = run_once(scratch_state)
    assert counts["sent"] == 1
    assert counts["delivered"] == 1


# --- replay ---------------------------------------------------------------


def test_replay_resets_to_pending(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    mark_dlq(scratch_state, rec.delivery_id, error="boom")
    replayed = replay(scratch_state, rec.delivery_id)
    assert replayed.status == DeliveryStatus.PENDING
    s = stats(scratch_state)
    assert s["replayed_total"] >= 1


def test_replay_already_delivered_rejected(scratch_state):
    from rollout_shield.webhook_delivery.replay import ReplayError
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    mark_status(scratch_state, rec.delivery_id, DeliveryStatus.DELIVERED)
    with pytest.raises(ReplayError):
        replay(scratch_state, rec.delivery_id)


def test_replay_all_dlq(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    ids = []
    for i in range(3):
        r = enqueue(scratch_state, target_name="t", payload={"i": i})
        mark_dlq(scratch_state, r.delivery_id, error=f"e{i}")
        ids.append(r.delivery_id)
    replayed = replay_all(scratch_state, DeliveryStatus.DLQ)
    assert sorted(replayed) == sorted(ids)
    for did in ids:
        rec = get_delivery(scratch_state, did)
        assert rec.status == DeliveryStatus.PENDING


# --- stats ----------------------------------------------------------------


def test_stats_initial(scratch_state):
    s = stats(scratch_state)
    assert s["outbox_depth"] == 0
    assert s["dlq_depth"] == 0
    assert s["enqueued_total"] == 0


def test_stats_after_enqueue(scratch_state):
    add_target(scratch_state, name="t", url="https://x/h")
    rec = enqueue(scratch_state, target_name="t", payload={"k": 1})
    s = stats(scratch_state)
    assert s["outbox_depth"] == 1
    assert s["enqueued_total"] == 1
    mark_dlq(scratch_state, rec.delivery_id, error="boom")
    s = stats(scratch_state)
    assert s["dlq_depth"] == 1
    assert s["dlq_total"] == 1


def test_default_dedupe_window_constant():
    assert DEFAULT_DEDUPE_WINDOW_SECONDS == 300
