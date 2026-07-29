"""Unit tests for the rollout-shield state layer."""

from __future__ import annotations

import json
import time


def test_state_root_default_is_home_rollout_shield():
    from rollout_shield.state import DEFAULT_STATE_ROOT, State
    assert DEFAULT_STATE_ROOT.name == ".rollout-shield"
    s = State()  # default root
    assert s.root == DEFAULT_STATE_ROOT


def test_state_root_override(scratch_state_root):
    from rollout_shield.state import State
    s = State(root=scratch_state_root)
    assert s.root == scratch_state_root


def test_state_atomic_write_json_roundtrip(scratch_state_root):
    from rollout_shield.state import SCHEMA_VERSION, atomic_write_json
    target = scratch_state_root / "config.json"
    payload = {"schema_version": SCHEMA_VERSION, "ok": True}
    atomic_write_json(target, payload)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == payload


def test_state_atomic_write_json_creates_parents(tmp_path):
    from rollout_shield.state import atomic_write_json
    deep = tmp_path / "a" / "b" / "c" / "config.json"
    atomic_write_json(deep, {"x": 1})
    assert deep.exists()


def test_state_summary_keys_present(scratch_state):
    summary = scratch_state.summary()
    assert summary["schema_version"] == 1
    assert "state_root" in summary
    assert summary["agents"]["total"] == 0
    assert summary["claims_count"] == 0
    assert summary["alerts_count"] == 0


def test_state_keys_list_empty_initially(scratch_state):
    assert scratch_state.list_keys() == []


def test_state_save_load_config(scratch_state):
    scratch_state.save_config({"foo": "bar", "n": 1})
    cfg = scratch_state.load_config()
    assert cfg["foo"] == "bar"
    assert cfg["n"] == 1


def test_state_append_claim_returns_path(scratch_state):
    claim = {
        "id": "clm_test_00000001",
        "schema": "rollout-shield.claim/v1",
        "type": "intent",
        "agent_id": "test-agent",
        "ts": int(time.time()),
        "body": "test",
        "parent": None,
        "signing": {"key_id": "agk_test_x", "algorithm": "Ed25519",
                    "public_key_pem": "x", "signature": "x",
                    "canonicalization": "json-stable"},
    }
    path = scratch_state.append_claim(claim)
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip()


def test_state_iter_claims_returns_newest_first(scratch_state):
    for i, ts in enumerate([100, 200, 150]):
        c = {"id": f"clm_test_{i}", "ts": ts, "type": "intent",
             "agent_id": "a", "body": "", "parent": None,
             "signing": {}, "schema": "rollout-shield.claim/v1"}
        scratch_state.append_claim(c)
    claims = list(scratch_state.iter_claims(limit=10))
    assert len(claims) == 3
    assert claims[0]["ts"] == 200  # newest first
    assert claims[1]["ts"] == 150
    assert claims[2]["ts"] == 100


def test_state_get_key_missing_returns_none(scratch_state):
    assert scratch_state.get_key("agk_nonexistent") is None


def test_state_put_key_then_get(scratch_state):
    meta = {"id": "agk_test_x", "agent_id": "a", "fingerprint": "fp"}
    scratch_state.put_key("agk_test_x", meta)
    fetched = scratch_state.get_key("agk_test_x")
    assert fetched["fingerprint"] == "fp"


def test_state_append_health(scratch_state):
    summary = {"status": "healthy", "ts": int(time.time()),
               "total": 1, "ok": 1, "degraded": 0, "checks": []}
    scratch_state.append_health(summary)
    latest = scratch_state.latest_health()
    assert latest["status"] == "healthy"
    assert latest["ok"] == 1
