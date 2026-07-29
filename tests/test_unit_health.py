"""Unit tests for health checks (state + repo)."""

from __future__ import annotations


def test_state_root_writable_passes(scratch_state):
    from rollout_shield.health_checks import check_state_root_writable
    r = check_state_root_writable(scratch_state)
    assert r.ok
    assert "writable" in r.message.lower()


def test_disk_space_passes(scratch_state):
    from rollout_shield.health_checks import check_disk_space
    r = check_disk_space(scratch_state, min_free_mb=1)
    assert r.ok


def test_keys_present_fails_when_no_keys(scratch_state):
    from rollout_shield.health_checks import check_keys_present
    r = check_keys_present(scratch_state)
    assert not r.ok
    assert "0" in r.message  # zero keys


def test_keys_present_passes_with_key(scratch_state):
    from rollout_shield.health_checks import check_keys_present
    scratch_state.put_key("agk_test_x", {"id": "agk_test_x", "agent_id": "a"})
    r = check_keys_present(scratch_state)
    assert r.ok


def test_recent_claims_fails_when_none(scratch_state):
    from rollout_shield.health_checks import check_recent_claims
    r = check_recent_claims(scratch_state)
    assert not r.ok


def test_loopback_reachable_passes(scratch_state):
    from rollout_shield.health_checks import check_self_reachable
    r = check_self_reachable(scratch_state)
    assert r.ok
    assert r.name == "loopback_reachable"


def test_aggregate_healthy_when_all_pass(scratch_state):
    from rollout_shield.health_checks import (
        aggregate,
        check_keys_present,
        check_self_reachable,
    )
    # patch a passing check onto the scratch state
    scratch_state.put_key("agk_test_x", {"id": "agk_test_x", "agent_id": "a"})
    results = [
        check_keys_present(scratch_state).to_dict(),
        check_self_reachable(scratch_state).to_dict(),
    ]
    summary = aggregate(results)
    assert summary["status"] == "healthy"
    assert summary["ok"] == 2


def test_aggregate_unhealthy_when_all_fail(scratch_state):
    from rollout_shield.health_checks import aggregate, check_keys_present, check_recent_claims
    results = [
        check_keys_present(scratch_state).to_dict(),
        check_recent_claims(scratch_state).to_dict(),
    ]
    summary = aggregate(results)
    assert summary["status"] == "unhealthy"
    assert summary["degraded"] == 2


def test_controller_policy_default_shared(scratch_state):
    from rollout_shield.space import load_policy
    assert load_policy(scratch_state) == "shared"


def test_controller_policy_set_and_load(scratch_state):
    from rollout_shield.space import load_policy, save_policy
    save_policy(scratch_state, "device-only", backup=False)
    assert load_policy(scratch_state) == "device-only"


def test_controller_policy_rejects_unknown(scratch_state):
    import pytest

    from rollout_shield.space import save_policy
    with pytest.raises(ValueError):
        save_policy(scratch_state, "garbage")


def test_controller_policy_check_health_fails_when_violated(scratch_state):
    from rollout_shield.health_checks import check_controller_policy
    from rollout_shield.space import save_policy
    save_policy(scratch_state, "device-only", backup=False)
    # add a non-device key — this is a violation under device-only
    scratch_state.put_key("agk_human_y", {
        "id": "agk_human_y", "agent_id": "h", "hardware_anchored": False,
    })
    r = check_controller_policy(scratch_state)
    assert not r.ok
    assert "device-only" in r.message
