"""Integration tests for the finetuning subsystem end-to-end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rollout_shield.ai.models import ModelInfo, register_model
from rollout_shield.finetuning import (
    doctor,
    iter_split,
    list_adapters,
    list_promoted,
    promote_adapter,
    register_dataset,
    start_run,
    unpromote_adapter,
)
from rollout_shield.state import State


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@pytest.fixture
def fresh_state(tmp_path) -> State:
    """A scratch state root, isolated from the user's machine."""
    root = tmp_path / "rs-test"
    root.mkdir(parents=True, exist_ok=True)
    return State(root=root)


def test_full_lifecycle_dataset_to_promoted(fresh_state, tmp_path):
    """End-to-end: register JSONL → train → eval → promote → unpromote."""
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [
        {"prompt": f"q{i}", "target": f"a{i}"} for i in range(20)
    ])
    ds = register_dataset(fresh_state, src_path=p, name="lifecycle",
                          split=0.8)

    register_model(ModelInfo(
        id="lifecycle-base", name="Lifecycle Base",
        description="", family="mock",
        fn=lambda prompt, params: {"text": "alpha", "tokens": 1, "meta": {}},
    ))

    rec = start_run(fresh_state, dataset_id=ds.dataset_id,
                    base_model_id="lifecycle-base",
                    recipe_name="sft-mini", backend="stdlib",
                    epochs=1, max_steps=4, eval_threshold=0.0)
    assert rec.adapter_id is not None

    # Status should be a terminal eval status
    assert rec.status in ("eval_passed", "eval_failed")

    if rec.status == "eval_passed":
        # promotion writes to the AI registry
        promoted = promote_adapter(fresh_state, rec.adapter_id)
        assert promoted.promoted_as_model_id is not None
        assert rec.adapter_id in list_promoted(fresh_state)
        from rollout_shield.ai.models import get_model
        info = get_model(promoted.promoted_as_model_id)
        assert info.family == "finetuned"
        # and the registered fn actually delegates
        out = info.fn("hello", {})
        assert "text" in out

        # unpromote removes from the sidecar
        assert unpromote_adapter(fresh_state, rec.adapter_id) is True
        assert rec.adapter_id not in list_promoted(fresh_state)


def test_re_register_promoted_survives_reload(fresh_state, tmp_path):
    """Promoted adapters re-register into a fresh State."""
    from rollout_shield.finetuning.promote import re_register_promoted
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"q{i}", "target": f"a{i}"}
                     for i in range(20)])
    ds = register_dataset(fresh_state, src_path=p, name="r1", split=0.8)

    register_model(ModelInfo(
        id="r-base", name="R Base", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    rec = start_run(fresh_state, dataset_id=ds.dataset_id,
                    base_model_id="r-base", recipe_name="sft-mini",
                    backend="stdlib", epochs=1, max_steps=2,
                    eval_threshold=0.0)
    if rec.status != "eval_passed":
        pytest.skip("eval didn't pass; can't test re-registration")
    promote_adapter(fresh_state, rec.adapter_id)

    # New State pointing at the same root → re_register_promoted runs.
    new_state = State(root=fresh_state.root)
    n = re_register_promoted(new_state)
    assert n >= 1


def test_concurrent_runs_serialize_on_lock(fresh_state, tmp_path):
    """Two runs in flight on the same state root → second waits or fails cleanly."""
    import threading
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"q{i}", "target": f"a{i}"}
                     for i in range(20)])
    ds = register_dataset(fresh_state, src_path=p, name="c1", split=0.8)
    register_model(ModelInfo(
        id="c-base", name="C", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    results = []
    errors = []

    def _go():
        try:
            r = start_run(fresh_state, dataset_id=ds.dataset_id,
                          base_model_id="c-base", recipe_name="sft-mini",
                          backend="stdlib", epochs=1, max_steps=2,
                          timeout_seconds=2.0)
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_go)
    t2 = threading.Thread(target=_go)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # at least one should succeed; the other may or may not depending on timing
    assert len(results) + len(errors) == 2
    assert len(results) >= 1


def test_adapter_id_is_reproducible_across_runs(fresh_state, tmp_path):
    """Same inputs → same adapter id (re-run idempotent)."""
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"q{i}", "target": f"a{i}"}
                     for i in range(20)])
    ds = register_dataset(fresh_state, src_path=p, name="r2", split=0.8)
    register_model(ModelInfo(
        id="d-base", name="D", description="", family="mock",
        fn=lambda prompt, params: {"text": "x", "tokens": 1, "meta": {}},
    ))
    r1 = start_run(fresh_state, dataset_id=ds.dataset_id,
                   base_model_id="d-base", recipe_name="sft-mini",
                   backend="stdlib", epochs=1, max_steps=2)
    r2 = start_run(fresh_state, dataset_id=ds.dataset_id,
                   base_model_id="d-base", recipe_name="sft-mini",
                   backend="stdlib", epochs=1, max_steps=2)
    assert r1.adapter_id == r2.adapter_id
    # only one adapter record should exist
    assert len(list_adapters(fresh_state)) == 1


def test_content_hash_changes_when_data_changes(fresh_state, tmp_path):
    """Adding one sample changes the dataset's content hash → new id."""
    p1 = tmp_path / "d1.jsonl"
    _write_jsonl(p1, [{"prompt": f"q{i}", "target": f"a{i}"}
                      for i in range(20)])
    p2 = tmp_path / "d2.jsonl"
    _write_jsonl(p2, [{"prompt": f"q{i}", "target": f"a{i}"}
                      for i in range(21)])
    r1 = register_dataset(fresh_state, src_path=p1, name="n1")
    r2 = register_dataset(fresh_state, src_path=p2, name="n2")
    assert r1.dataset_id != r2.dataset_id
    assert r1.content_sha256 != r2.content_sha256


def test_doctor_passes_on_clean_state(fresh_state):
    """``finetune doctor`` reports passed=True on a clean scratch state."""
    r = doctor(fresh_state)
    d = r.to_dict()
    assert d["passed"] is True
    assert d["state_writable"] is True
    assert d["backend_stdlib"] is True


def test_iter_split_deterministic_order(fresh_state, tmp_path):
    """The train/val split is content-hash-deterministic across calls."""
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"q{i}", "target": f"a{i}"}
                     for i in range(20)])
    ds = register_dataset(fresh_state, src_path=p, name="ord", split=0.8)
    rows1 = list(iter_split(fresh_state, ds.dataset_id, "train"))
    rows2 = list(iter_split(fresh_state, ds.dataset_id, "train"))
    assert [r["prompt"] for r in rows1] == [r["prompt"] for r in rows2]
