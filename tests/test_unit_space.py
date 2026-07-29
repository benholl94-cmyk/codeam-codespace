"""Unit tests for the controller policy (space) module."""

from __future__ import annotations


def test_load_policy_default(scratch_state):
    from rollout_shield.space import load_policy
    assert load_policy(scratch_state) == "shared"


def test_load_policy_unknown_falls_back_to_shared(scratch_state):
    from rollout_shield.space import load_policy
    scratch_state.save_config({"controller_policy": "garbage"})
    assert load_policy(scratch_state) == "shared"


def test_save_policy_returns_backup_path(scratch_state):
    from rollout_shield.space import load_policy, save_policy
    backup = save_policy(scratch_state, "device-only", backup=True)
    assert backup is not None
    assert backup.exists()
    assert backup.name.startswith("config.json.bak.")
    assert load_policy(scratch_state) == "device-only"


def test_save_policy_no_backup(scratch_state):
    from rollout_shield.space import save_policy
    backup = save_policy(scratch_state, "human-only", backup=False)
    assert backup is None


def test_check_key_allowed_shared(scratch_state):
    from rollout_shield.space import check_key_allowed
    allowed, reason = check_key_allowed("shared", {"hardware_anchored": False})
    assert allowed


def test_check_key_allowed_device_only_rejects_human(scratch_state):
    from rollout_shield.space import check_key_allowed
    allowed, reason = check_key_allowed("device-only", {"hardware_anchored": False})
    assert not allowed
    assert "hardware_anchored" in reason


def test_check_key_allowed_device_only_accepts_device(scratch_state):
    from rollout_shield.space import check_key_allowed
    allowed, reason = check_key_allowed("device-only", {"hardware_anchored": True})
    assert allowed


def test_check_key_allowed_human_only_rejects_device(scratch_state):
    from rollout_shield.space import check_key_allowed
    allowed, reason = check_key_allowed("human-only", {"hardware_anchored": True})
    assert not allowed


def test_quarantine_key_marks_quarantined(scratch_state):
    from rollout_shield.space import quarantine_key, unquarantine_key
    scratch_state.put_key("agk_test_x", {"id": "agk_test_x", "agent_id": "a"})
    assert quarantine_key(scratch_state, "agk_test_x", reason="test")
    meta = scratch_state.get_key("agk_test_x")
    assert meta["quarantined"]
    assert meta["quarantine_reason"] == "test"
    # unquarantine
    assert unquarantine_key(scratch_state, "agk_test_x")
    meta = scratch_state.get_key("agk_test_x")
    assert "quarantined" not in meta


def test_quarantine_is_idempotent(scratch_state):
    from rollout_shield.space import quarantine_key
    scratch_state.put_key("agk_test_x", {"id": "agk_test_x"})
    assert quarantine_key(scratch_state, "agk_test_x", reason="x")
    assert quarantine_key(scratch_state, "agk_test_x", reason="x")  # idempotent


def test_validate_space_consistent_when_shared(scratch_state):
    from rollout_shield.space import validate_space
    scratch_state.put_key("agk_human_x", {"id": "agk_human_x", "hardware_anchored": False})
    consistent, violations = validate_space(scratch_state)
    assert consistent


def test_validate_space_flags_violation_under_device_only(scratch_state):
    from rollout_shield.space import save_policy, validate_space
    save_policy(scratch_state, "device-only", backup=False)
    scratch_state.put_key("agk_human_x", {"id": "agk_human_x", "hardware_anchored": False})
    consistent, violations = validate_space(scratch_state)
    assert not consistent
    assert any(v[0] == "error" for v in violations)


def test_latest_key_for_agent_newest_wins(scratch_state):
    from rollout_shield.space import latest_key_for_agent
    scratch_state.put_key("agk_a_1", {"id": "agk_a_1", "agent_id": "a", "created_at": 100})
    scratch_state.put_key("agk_a_2", {"id": "agk_a_2", "agent_id": "a", "created_at": 200})
    latest = latest_key_for_agent(scratch_state, "a")
    assert latest["id"] == "agk_a_2"


def test_latest_key_for_agent_no_keys(scratch_state):
    from rollout_shield.space import latest_key_for_agent
    assert latest_key_for_agent(scratch_state, "missing") is None


def test_space_info_collects_stats(scratch_state):
    from rollout_shield.space import save_policy, space_info
    save_policy(scratch_state, "shared", backup=False)
    scratch_state.put_key("agk_a", {"id": "agk_a", "agent_id": "a", "hardware_anchored": True})
    scratch_state.put_key("agk_b", {"id": "agk_b", "agent_id": "b", "hardware_anchored": False})
    info = space_info(scratch_state)
    assert info.policy == "shared"
    assert info.total_keys == 2
    assert info.device_keys == 1
    assert info.human_keys == 1
