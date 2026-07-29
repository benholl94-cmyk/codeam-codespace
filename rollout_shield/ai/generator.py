"""First-of-kind content generator.

A "First-of-kind" artifact is a piece of content that has never been
generated before — at least, not with this exact combination of
(prompt, kind, parameters). Each artifact is fingerprinted (sha256)
and tagged with a deterministic id:

    fk_<short-hash>

Re-running the generator with the same (prompt, kind, params)
produces the same id (deterministic). Different prompts produce
different ids.

Artifact kinds:

- ``poem``       — short poem (mock_creative)
- ``slogan``     — short product slogan
- ``code``       — Python function stub (mock_code)
- ``structured`` — JSON-shaped record (mock_structured)
- ``summary``    — a paragraph summary

The router runs all kinds of models in parallel and picks the
best-scoring output (per the leaderboard) as the First-of-kind
content for that kind.

Storage: ``<state_root>/ai/first_of_kind/<fk_id>.json``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..state import State
from .router import route

KIND_TO_MODEL = {
    "poem": "mock-creative",
    "slogan": "mock-creative",
    "code": "mock-code",
    "structured": "mock-structured",
    "summary": "mock-deterministic",
}


@dataclass
class FirstOfKind:
    id: str
    kind: str
    prompt: str
    prompt_digest: str
    text: str
    model_id: str
    route_strategy: str
    ts: int
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(prompt: str, kind: str, params: dict) -> str:
    h = hashlib.sha256()
    h.update(kind.encode("utf-8"))
    h.update(b"\x00")
    canonical_params = json.dumps(params, sort_keys=True, ensure_ascii=False)
    h.update(canonical_params.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:16]


def first_of_kind_id(prompt: str, kind: str, params: dict | None = None) -> str:
    """Compute the deterministic id for a First-of-kind artifact."""
    digest = _digest(prompt, kind, params or {})
    return "fk_" + digest.split(":", 1)[1]


def generate(state: State, prompt: str, kind: str = "poem",
             params: dict | None = None,
             tags: list[str] | None = None) -> FirstOfKind:
    """Generate a First-of-kind artifact.

    The artifact id is deterministic on (prompt, kind, params), so
    repeated calls with the same inputs return the same id. The
    generator runs the request through the parallel-lateral router
    so the chosen output is the leaderboard-best model for this kind.
    """
    params = params or {}
    kind = kind if kind in KIND_TO_MODEL else "summary"
    fk_id = first_of_kind_id(prompt, kind, params)
    path = _artifact_path(state, fk_id)
    if path.exists():
        # idempotent: return the previously stored artifact
        try:
            return FirstOfKind(**json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    # Build the prompt the router will send to the models
    from .leaderboard import aggregate_scores
    benchmark_scores = aggregate_scores(state)
    trace = route(prompt=prompt,
                  models=list({KIND_TO_MODEL[kind], "mock-deterministic"}),
                  strategy="best",
                  benchmark_scores=benchmark_scores,
                  max_workers=2,
                  state=state)
    text = trace.selected_text or ""
    model_id = trace.selected or KIND_TO_MODEL[kind]

    artifact = FirstOfKind(
        id=fk_id,
        kind=kind,
        prompt=prompt,
        prompt_digest=_digest(prompt, kind, params).split(":", 1)[1],
        text=text,
        model_id=model_id,
        route_strategy=trace.strategy,
        ts=int(time.time()),
        tags=tags or [],
        meta={
            "router_elapsed_ms": round(trace.elapsed_ms, 2),
            "router_outputs": len(trace.outputs),
        },
    )
    _save_artifact(state, artifact)
    return artifact


def _artifact_path(state: State, fk_id: str) -> Path:
    p = state.root / "ai" / "first_of_kind"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{fk_id}.json"


def _save_artifact(state: State, artifact: FirstOfKind) -> Path:
    path = _artifact_path(state, artifact.id)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
    return path


def iter_artifacts(state: State, limit: int | None = None,
                   kind: str | None = None) -> list[FirstOfKind]:
    root = state.root / "ai" / "first_of_kind"
    if not root.exists():
        return []
    out: list[FirstOfKind] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            artifact = FirstOfKind(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if kind and artifact.kind != kind:
            continue
        out.append(artifact)
        if limit is not None and len(out) >= limit:
            break
    return out


def get_artifact(state: State, fk_id: str) -> FirstOfKind | None:
    path = _artifact_path(state, fk_id)
    if not path.exists():
        return None
    try:
        return FirstOfKind(**json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
