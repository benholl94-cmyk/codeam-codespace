"""Unit tests for the AI layer."""

from __future__ import annotations

import pytest


def test_list_models_includes_mocks_and_own():
    from rollout_shield.ai.models import list_models
    models = list_models()
    ids = {m.id for m in models}
    # 4 mocks + 5 own models
    assert "mock-deterministic" in ids
    assert "mock-creative" in ids
    assert "mock-structured" in ids
    assert "mock-code" in ids
    assert "rollout-model" in ids
    assert "verifier-model" in ids
    assert "contradictor-model" in ids
    assert "repo-aware-model" in ids
    assert "spec-citation-model" in ids


def test_get_model_unknown_raises():
    from rollout_shield.ai.models import get_model
    with pytest.raises(KeyError):
        get_model("does-not-exist")


def test_mock_deterministic_output_is_stable():
    from rollout_shield.ai.models import mock_deterministic
    prompt = "stable output test"
    a = mock_deterministic(prompt, {})
    b = mock_deterministic(prompt, {})
    assert a["text"] == b["text"]


def test_mock_structured_returns_json():
    from rollout_shield.ai.models import mock_structured
    import json
    out = mock_structured("test", {})
    parsed = json.loads(out["text"])
    assert parsed["kind"] == "structured-response"
    assert "digest" in parsed


def test_mock_code_returns_function_def():
    from rollout_shield.ai.models import mock_code
    out = mock_code("test", {})
    assert "def f_" in out["text"]
    assert "return" in out["text"]


def test_router_runs_models_in_parallel(scratch_state):
    from rollout_shield.ai.router import route
    trace = route(prompt="parallel test", models=["mock-deterministic", "mock-structured"],
                  strategy="concat", max_workers=2, state=scratch_state)
    assert len(trace.outputs) == 2
    assert all(o["ok"] for o in trace.outputs)


def test_router_strategy_first():
    from rollout_shield.ai.models import list_models
    from rollout_shield.ai.router import route
    trace = route(prompt="first", models=["mock-deterministic"], strategy="first")
    assert trace.selected == "mock-deterministic"


def test_router_strategy_concat():
    from rollout_shield.ai.router import route
    trace = route(prompt="concat", models=["mock-deterministic", "mock-structured"],
                  strategy="concat")
    assert trace.selected == "concat"
    assert "===" in trace.selected_text


def test_rollout_model_drafts_change_with_parent(scratch_state):
    from rollout_shield.ai.own_models import rollout_model
    # Add an intent claim to the scratch state
    scratch_state.append_claim({
        "id": "clm_intent_1", "schema": "rollout-shield.claim/v1",
        "type": "intent", "agent_id": "rollout-model",
        "ts": 1000, "body": "the intent", "parent": None,
        "signing": {},
    })
    out = rollout_model("draft a change for this", {"state": scratch_state})
    assert '"type": "change"' in out["text"] or '"type":"change"' in out["text"]
    assert "clm_intent_1" in out["text"]  # parent linkage
    assert out["meta"]["parent_id"] == "clm_intent_1"


def test_verifier_model_no_claim_returns_not_found(scratch_state):
    from rollout_shield.ai.own_models import verifier_model
    out = verifier_model("clm_does_not_exist", {"state": scratch_state})
    assert "found" in out["text"]
    assert not out["meta"]["signature_ok"]


def test_repo_aware_model_finds_self_heal(scratch_state):
    from rollout_shield.ai.own_models import repo_aware_model
    out = repo_aware_model("self-heal", {"state": scratch_state})
    # the test runs from inside the repo, so the model should find self_heal.py
    assert "self_heal" in out["text"] or "self-heal" in out["text"]


def test_spec_citation_model_returns_citations(scratch_state):
    from rollout_shield.ai.own_models import spec_citation_model
    out = spec_citation_model("claim", {"state": scratch_state})
    assert "citations" in out["text"]
    assert out["meta"]["citations"] >= 0  # may be 0 if no specs match
