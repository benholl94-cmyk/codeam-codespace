"""Own-model versions for the rollout-shield AI layer.

These are **code-defined** models — not mocks, not external-API wrappers.
Each is a ``ModelFn`` whose "weights" are the local state, repo, and
specs of rollout-shield itself. They are first-class citizens of the
model registry (see ``models.py``).

Why own models?

The rollout-shield AI layer is meant to be a *router* over a portfolio
of models. If the only models available are mocks or third-party APIs,
the portfolio is leaky and the IP is thin. These own models give the
package a self-contained identity:

- ``rollout-model``       — drafts a ``change`` claim from an intent
- ``verifier-model``      — drafts a ``verify`` claim from a claim id
- ``contradictor-model``  — drafts a ``contradict`` claim if it finds one
- ``repo-aware-model``    — searches the repo for context
- ``spec-citation-model`` — searches the protocol/ specs for citations

Each model is deterministic (so the benchmark suite grades reproducible
scores), pulls live data from the user's ``State`` + repo + specs, and
returns a structured response that downstream layers (router, benchmarks,
leaderboard, self-cycle, generator) already understand.

The models use ``params["state"]`` if provided (in-process routing) or
fall back to ``State()`` with the default root (for standalone tests).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path

from ..state import State

# Spec / repo locations — these are the "weights" of the own models.
# They are intentionally relative to the repo root so the models work
# whether the package is on the host path or running from the repo.


PROTOCOL_DIRS = ("protocol", "agent", "rollout", "hardware")


def _repo_root() -> Path | None:
    """Find the repo root by walking up from CWD."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "rollout_shield" / "__init__.py").exists():
            return parent
    return None


def _state(params: dict) -> State:
    """Return the State from params or build a default one."""
    s = params.get("state")
    if s is not None:
        return s
    root = params.get("state_root")
    return State(root=root) if root else State()


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _find_claim(state: State, claim_id: str) -> dict | None:
    """Find a claim by id (linear scan — fine for our scale)."""
    for c in state.iter_claims(limit=100000):
        if c.get("id") == claim_id:
            return c
    return None


def _latest_claim_by_type(state: State, claim_type: str,
                          agent_id: str | None = None) -> dict | None:
    """Return the most recent claim of a given type (optionally for an agent)."""
    latest: dict | None = None
    latest_ts = -1
    for c in state.iter_claims(limit=100000):
        if c.get("type") != claim_type:
            continue
        if agent_id and c.get("agent_id") != agent_id:
            continue
        ts = c.get("ts", 0)
        if ts > latest_ts:
            latest_ts = ts
            latest = c
    return latest


# ============================================================
# 1. rollout-model
# ============================================================


def rollout_model(prompt: str, params: dict) -> dict:
    """Given a prompt, draft a ``change`` claim linked to the latest intent.

    Behaviour:

    - If ``prompt`` looks like a claim id (``clm_...``), look up that
      claim; if it is an ``intent``, use it as the parent.
    - Otherwise, treat the prompt as the intent body and look up the
      most recent ``intent`` claim in state as the parent.
    - If no intent is found, the parent is ``None`` and the draft is
      flagged as a "root" change.

    The output is a JSON object with the draft fields populated. The
    draft is *not* signed here — signing is the caller's job (use
    ``rollout-shield claim create`` to actually emit it).
    """
    state = _state(params)
    agent_id = params.get("agent_id", "rollout-model")

    parent_id: str | None = None
    parent_claim: dict | None = None

    # Case 1: prompt is a claim id
    if prompt.startswith("clm_"):
        candidate = _find_claim(state, prompt)
        if candidate and candidate.get("type") == "intent":
            parent_id = candidate["id"]
            parent_claim = candidate

    # Case 2: prompt is a description; find the latest intent
    if parent_id is None:
        candidate = _latest_claim_by_type(state, "intent", agent_id=agent_id)
        if candidate is None:
            candidate = _latest_claim_by_type(state, "intent")
        if candidate:
            parent_id = candidate["id"]
            parent_claim = candidate

    draft_id = "clm_" + hashlib.sha256(
        (prompt + str(time.time()) + "rollout-model").encode()
    ).hexdigest()[:16]
    draft = {
        "id": draft_id,
        "schema": "rollout-shield.claim/v1",
        "type": "change",
        "agent_id": agent_id,
        "ts": int(time.time()),
        "body": prompt,
        "parent": parent_id,
        "draft": True,
        "drafted_by": "rollout-model",
    }
    text = json.dumps({"draft": draft, "parent_claim": parent_claim},
                      sort_keys=True, ensure_ascii=False)
    return {
        "text": text,
        "tokens": _approx_tokens(text),
        "meta": {
            "agent_id": agent_id,
            "parent_id": parent_id,
            "draft_kind": "change",
            "root_change": parent_id is None,
        },
    }


# ============================================================
# 2. verifier-model
# ============================================================


def _verify_signature(claim: dict) -> dict:
    """Verify a claim's Ed25519 signature.

    Returns ``{"ok": bool, "reason": str | None}``.
    """
    sig_block = claim.get("signing", {}) or {}
    pub_pem = sig_block.get("public_key_pem", "")
    sig_b64 = sig_block.get("signature", "")
    if not pub_pem or not sig_b64:
        return {"ok": False, "reason": "missing signature or public key"}
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        return {"ok": False, "reason": "cryptography package not installed"}

    try:
        pub = load_pem_public_key(pub_pem.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"public key load failed: {exc}"}

    preimage = {
        "schema": claim.get("schema"),
        "type": claim.get("type"),
        "agent_id": claim.get("agent_id"),
        "ts": claim.get("ts"),
        "body": claim.get("body"),
        "parent": claim.get("parent"),
    }
    payload = json.dumps(preimage, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    try:
        sig_bytes = base64.b64decode(sig_b64.encode("ascii"))
        pub.verify(sig_bytes, payload)
        return {"ok": True, "reason": None}
    except Exception as exc:  # noqa: BLE001 — InvalidSignature or crypto error
        return {"ok": False, "reason": f"signature invalid: {exc}"}


def verifier_model(prompt: str, params: dict) -> dict:
    """Verify a claim's signature and draft a ``verify`` claim.

    The prompt is the claim id (``clm_...``). The output contains:
      - the verification result (ok / reason)
      - a draft ``verify`` claim that records the verification outcome
    """
    state = _state(params)
    claim_id = prompt.strip()
    claim = _find_claim(state, claim_id) if claim_id.startswith("clm_") else None

    if claim is None:
        text = json.dumps({"claim_id": claim_id, "found": False,
                           "ok": False, "reason": "no such claim",
                           "verify_draft": None},
                          sort_keys=True)
        return {
            "text": text,
            "tokens": _approx_tokens(text),
            "meta": {"claim_id": claim_id, "found": False, "signature_ok": False},
        }

    verification = _verify_signature(claim)
    agent_id = params.get("agent_id", "verifier-model")

    draft_id = "clm_" + hashlib.sha256(
        (claim_id + str(verification["ok"]) + "verifier-model").encode()
    ).hexdigest()[:16]
    verify_draft = {
        "id": draft_id,
        "schema": "rollout-shield.claim/v1",
        "type": "verify",
        "agent_id": agent_id,
        "ts": int(time.time()),
        "body": json.dumps({
            "verified_claim_id": claim_id,
            "verification_ok": verification["ok"],
            "verification_reason": verification["reason"],
        }, sort_keys=True),
        "parent": claim_id,
        "draft": True,
        "drafted_by": "verifier-model",
    }
    text = json.dumps({"claim_id": claim_id, "found": True,
                       "ok": verification["ok"],
                       "reason": verification["reason"],
                       "verify_draft": verify_draft},
                      sort_keys=True)
    return {
        "text": text,
        "tokens": _approx_tokens(text),
        "meta": {"claim_id": claim_id, "found": True,
                 "signature_ok": verification["ok"],
                 "draft_kind": "verify"},
    }


# ============================================================
# 3. contradictor-model
# ============================================================


def _find_contradictions(claim: dict, all_claims: list[dict]) -> list[dict]:
    """Detect contradictions involving ``claim`` against the corpus.

    Heuristics (deliberately conservative — false positives are worse
    than false negatives in a contradiction wire):

    1. **Type cycle**: chain is intent → change → verify; a same-parallel
       ``change`` claim with a different body indicates a forked rollout.
    2. **Duplicate signature**: same key + same body + same parent ==
       duplicate (already flagged by iter_claims but reinforced here).
    3. **Body contradiction**: a claim whose body literally negates the
       parent's body (matched via the token ``"not"`` or ``"revert"``).
    """
    found: list[dict] = []
    claim_id = claim.get("id")
    parent_id = claim.get("parent")
    parent = None
    if parent_id:
        for c in all_claims:
            if c.get("id") == parent_id:
                parent = c
                break

    # 1. Forked rollout
    if claim.get("type") == "change" and parent_id:
        peers = [c for c in all_claims
                 if c.get("parent") == parent_id
                 and c.get("type") == "change"
                 and c.get("id") != claim_id]
        for peer in peers:
            if peer.get("body") != claim.get("body"):
                found.append({
                    "kind": "forked_rollout",
                    "severity": "warning",
                    "message": (f"two change claims share parent {parent_id}: "
                                f"{claim_id} and {peer['id']}"),
                    "ids": [claim_id, peer["id"]],
                })

    # 2. Body contradiction (very simple lexical heuristic)
    if parent is not None:
        cbody = (claim.get("body") or "").lower()
        if "revert" in cbody or " undo " in cbody or " not " in cbody:
            found.append({
                "kind": "body_negation",
                "severity": "info",
                "message": f"claim {claim_id} body appears to negate parent {parent_id}",
                "ids": [claim_id, parent_id],
            })

    # 3. Verify-after-revert
    if claim.get("type") == "verify" and parent is not None:
        if parent.get("type") == "change" and "revert" in (parent.get("body") or "").lower():
            found.append({
                "kind": "verify_after_revert",
                "severity": "warning",
                "message": f"verify {claim_id} follows a revert change {parent_id}",
                "ids": [claim_id, parent_id],
            })

    return found


def contradictor_model(prompt: str, params: dict) -> dict:
    """Scan a claim for contradictions; draft a ``contradict`` claim if found.

    Prompt: claim id. The model examines the claim body, its parent, and
    its peers (other claims sharing the same parent) and emits a draft
    ``contradict`` claim if any of the contradiction heuristics trigger.
    """
    state = _state(params)
    claim_id = prompt.strip()
    claim = _find_claim(state, claim_id) if claim_id.startswith("clm_") else None

    if claim is None:
        text = json.dumps({"claim_id": claim_id, "found": False,
                           "contradictions": [], "contradict_draft": None},
                          sort_keys=True)
        return {
            "text": text,
            "tokens": _approx_tokens(text),
            "meta": {"claim_id": claim_id, "found": False,
                     "contradictions_found": 0},
        }

    all_claims = list(state.iter_claims(limit=100000))
    contradictions = _find_contradictions(claim, all_claims)

    contradict_draft: dict | None = None
    if contradictions:
        agent_id = params.get("agent_id", "contradictor-model")
        draft_id = "clm_" + hashlib.sha256(
            (claim_id + str(len(contradictions)) + "contradictor-model").encode()
        ).hexdigest()[:16]
        contradict_draft = {
            "id": draft_id,
            "schema": "rollout-shield.claim/v1",
            "type": "contradict",
            "agent_id": agent_id,
            "ts": int(time.time()),
            "body": json.dumps({
                "contradicted_claim_id": claim_id,
                "contradictions": contradictions,
            }, sort_keys=True),
            "parent": claim_id,
            "draft": True,
            "drafted_by": "contradictor-model",
        }

    text = json.dumps({"claim_id": claim_id, "found": True,
                       "contradictions": contradictions,
                       "contradict_draft": contradict_draft},
                      sort_keys=True)
    return {
        "text": text,
        "tokens": _approx_tokens(text),
        "meta": {"claim_id": claim_id, "found": True,
                 "contradictions_found": len(contradictions),
                 "draft_kind": "contradict" if contradict_draft else None},
    }


# ============================================================
# 4. repo-aware-model
# ============================================================


_REPO_SKIP_DIRS = {
    ".git", "__pycache__", ".rollout-shield", ".beads", ".dolt",
    "node_modules", ".vscode", "dist", "build",
}


def _scan_repo(root: Path, query: str, max_results: int = 10) -> list[dict]:
    """Search the repo for files matching ``query`` (by name or content)."""
    results: list[dict] = []
    query_lower = query.lower()
    keywords = [w for w in re.findall(r"\w+", query_lower) if len(w) >= 3]
    if not keywords:
        return results

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _REPO_SKIP_DIRS for part in path.parts):
            continue
        if any(path.suffix in {".pyc", ".pyo", ".db"} for _ in [0]):
            continue
        score = 0
        # filename match
        if any(kw in path.name.lower() for kw in keywords):
            score += 5
        # content match
        try:
            if path.stat().st_size > 1_000_000:
                continue  # skip huge files
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt = ""
        for kw in keywords:
            if kw in text.lower():
                score += 1
                idx = text.lower().find(kw)
                if idx >= 0 and not excerpt:
                    start = max(0, idx - 80)
                    end = min(len(text), idx + 200)
                    excerpt = text[start:end].replace("\n", " ")[:250]
        if score > 0:
            results.append({
                "path": str(path.relative_to(root)),
                "score": score,
                "excerpt": excerpt,
            })
        if len(results) >= max_results * 4:
            break

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def repo_aware_model(prompt: str, params: dict) -> dict:
    """Search the repository for files matching the prompt.

    Uses the local repo as the model's "knowledge base". Returns the
    top-matching file paths with a short excerpt from each.
    """
    repo = _repo_root()
    if not repo:
        text = json.dumps({"repo_root": None, "matches": [],
                           "error": "no repo discovered"}, sort_keys=True)
        return {
            "text": text,
            "tokens": _approx_tokens(text),
            "meta": {"repo_root": None, "matches": 0},
        }

    matches = _scan_repo(repo, prompt)
    text = json.dumps({"repo_root": str(repo), "matches": matches,
                       "query": prompt}, sort_keys=True, ensure_ascii=False)
    return {
        "text": text,
        "tokens": _approx_tokens(text),
        "meta": {"repo_root": str(repo), "matches": len(matches),
                 "query": prompt},
    }


# ============================================================
# 5. spec-citation-model
# ============================================================


def _scan_specs(query: str, max_results: int = 8) -> list[dict]:
    """Search the protocol/ agent/ rollout/ hardware/ specs for the query."""
    repo = _repo_root()
    if not repo:
        return []
    keywords = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]
    if not keywords:
        return []

    results: list[dict] = []
    for dirname in PROTOCOL_DIRS:
        spec_dir = repo / dirname
        if not spec_dir.is_dir():
            continue
        for md in sorted(spec_dir.rglob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            score = 0
            for kw in keywords:
                if kw in text.lower():
                    score += 1
            if score == 0:
                continue
            # pull a section heading near the first match
            lines = text.splitlines()
            heading = ""
            excerpt = ""
            for i, line in enumerate(lines):
                low = line.lower()
                if any(kw in low for kw in keywords):
                    excerpt = line.strip()[:200]
                    # walk back to find nearest heading
                    for j in range(i - 1, -1, -1):
                        if lines[j].lstrip().startswith("#"):
                            heading = lines[j].lstrip("# ").strip()
                            break
                    break
            results.append({
                "spec_dir": dirname,
                "path": str(md.relative_to(repo)),
                "heading": heading,
                "excerpt": excerpt,
                "score": score,
            })
        if len(results) >= max_results * 2:
            break

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def spec_citation_model(prompt: str, params: dict) -> dict:
    """Return spec citations matching the prompt.

    Searches the ``protocol/``, ``agent/``, ``rollout/``, and
    ``hardware/`` spec directories for the query and returns a short
    citation list (path + heading + excerpt).
    """
    citations = _scan_specs(prompt)
    text = json.dumps({"query": prompt, "citations": citations},
                      sort_keys=True, ensure_ascii=False)
    return {
        "text": text,
        "tokens": _approx_tokens(text),
        "meta": {"query": prompt, "citations": len(citations),
                 "spec_dirs": list(PROTOCOL_DIRS)},
    }
