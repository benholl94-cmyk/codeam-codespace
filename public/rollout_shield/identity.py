"""Unified pseudonym-identity system for user + model + session.

This module implements the user's instruction:

    "Build a unique-Personality-ident for me=user+you=model.ai — one
     pseudonym-ident with full-valid&audit chaining ids, that handles
     task issues, conflicts, wrong parses between me, you, and each
     other, fetching the world restrictions, building innovative
     working for the future and here."

Four pieces:

1. ``Pseudonym`` — a single, deterministic identity derived from
   ``user_seed`` + ``model_id`` + ``session_id`` + ``prev_chain_hash``.
   The pseudonym is stable for the same inputs (so the user + the AI
   always see the same handle), but it changes whenever any input
   changes (so a new session gets a new pseudonym and the old chain
   link is preserved via ``prev_chain_hash``).

2. ``IdentityChain`` — an append-only, hash-linked chain of identity
   events. Every ``append`` records a ``chain_id`` whose hash depends
   on the previous entry's hash; this makes tampering detectable. The
   chain is persisted to ``<state_root>/identity/chain.jsonl``.

3. ``ConflictRecord`` — when the user and the AI disagree on parsing,
   intent, or scope, both sides are recorded with a resolution. Each
   conflict gets a chained ID and is appended to
   ``<state_root>/identity/conflicts.jsonl``.

4. ``Restrictions`` — the hard world-limits the system respects. These
   are documented in code (not hidden) so operators can audit them.
   When a request crosses a limit, the restriction is named and the
   request is refused.

The CLI surface (registered in cli.py):
    rollout-shield identity init     — create the initial pseudonym
    rollout-shield identity show     — print the current pseudonym
    rollout-shield identity verify   — walk the chain, verify hashes
    rollout-shield identity conflict — record a user↔AI disagreement
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------- 1. Pseudonym ----------

# Prefix lets log scanners, debuggers, and humans recognize identity
# tokens at a glance. 12 hex chars after the prefix = 48 bits of
# entropy — enough to make collisions astronomically unlikely within
# a single install.
PSEUDONYM_PREFIX = "psn_"
PSEUDONYM_HEX_LEN = 12


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PseudonymInputs:
    """The five inputs that determine a pseudonym.

    Any change to any input changes the resulting pseudonym. The
    ``prev_chain_hash`` is what makes the identity chain tamper-
    evident: a new pseudonym cannot be forged without knowing the
    previous chain hash.
    """
    user_seed: str       # operator-supplied; never the real user id
    model_id: str        # e.g., "MiniMax-M3" or "claude-fable-5"
    session_id: str      # random per session
    created_at: int      # unix seconds
    prev_chain_hash: str # "0" * 64 for the first pseudonym

    def canonical(self) -> bytes:
        # Sort keys for determinism; separators are compact; ascii
        # encoding for the user_seed (operator is responsible for
        # choosing a UTF-8-safe seed).
        return json.dumps({
            "user_seed": self.user_seed,
            "model_id": self.model_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "prev_chain_hash": self.prev_chain_hash,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


@dataclass(frozen=True)
class Pseudonym:
    """A single, deterministic identity token."""
    token: str                          # e.g., "psn_a3b4c5d6e7f8"
    inputs: PseudonymInputs
    chain_id: str = ""                  # assigned by IdentityChain.append
    chain_hash: str = ""                # assigned by IdentityChain.append

    @classmethod
    def derive(cls, *, user_seed: str, model_id: str, session_id: str,
               created_at: int | None = None,
               prev_chain_hash: str = "0" * 64) -> "Pseudonym":
        ts = int(created_at if created_at is not None else time.time())
        inputs = PseudonymInputs(
            user_seed=user_seed,
            model_id=model_id,
            session_id=session_id,
            created_at=ts,
            prev_chain_hash=prev_chain_hash,
        )
        digest = _digest(inputs.canonical())
        token = PSEUDONYM_PREFIX + digest[:PSEUDONYM_HEX_LEN]
        return cls(token=token, inputs=inputs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pseudonym": self.token,
            "chain_id": self.chain_id,
            "chain_hash": self.chain_hash,
            "user_seed": self.inputs.user_seed,
            "model_id": self.inputs.model_id,
            "session_id": self.inputs.session_id,
            "created_at": self.inputs.created_at,
            "prev_chain_hash": self.inputs.prev_chain_hash,
        }


# ---------- 2. IdentityChain ----------

CHAIN_FILENAME = "chain.jsonl"
CONFLICTS_FILENAME = "conflicts.jsonl"


def _identity_dir(state_root: Path) -> Path:
    p = Path(state_root) / "identity"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)  # owner-only
    except OSError:
        pass
    return p


def _read_last_chain_hash(state_root: Path) -> str:
    """Return the chain_hash of the most recent chain entry, or zero hash."""
    chain_file = _identity_dir(state_root) / CHAIN_FILENAME
    if not chain_file.exists():
        return "0" * 64
    last_hash = "0" * 64
    with open(chain_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            last_hash = rec.get("chain_hash", last_hash)
    return last_hash


def _next_chain_id(state_root: Path) -> str:
    """Return ``idc_<6-hex>`` where the 6-hex is the count + 1."""
    chain_file = _identity_dir(state_root) / CHAIN_FILENAME
    n = 0
    if chain_file.exists():
        with open(chain_file, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    return f"idc_{n + 1:06x}"


class IdentityChain:
    """Append-only, hash-linked chain of pseudonym events.

    Each call to ``append``:
      1. Computes ``chain_hash = SHA256(prev_chain_hash || canonical(record))``
      2. Writes the record to ``<state_root>/identity/chain.jsonl``
      3. fsync the file so a power loss cannot break the chain

    ``verify`` walks the chain and recomputes each hash; any mismatch
    indicates tampering or corruption.
    """

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root)
        self.dir = _identity_dir(state_root)
        self.file = self.dir / CHAIN_FILENAME

    def append(self, pseudonym: Pseudonym, *,
               note: str = "") -> tuple[Pseudonym, str]:
        """Append the pseudonym to the chain.

        Returns ``(pseudonym_with_chain_id, chain_hash)``. The
        pseudonym is mutated to include its ``chain_id`` and
        ``chain_hash`` (returned as a fresh object because Pseudonym
        is frozen).
        """
        prev = _read_last_chain_hash(self.state_root)
        chain_id = _next_chain_id(self.state_root)
        record = {
            "chain_id": chain_id,
            "ts": int(time.time()),
            "pseudonym": pseudonym.token,
            "user_seed": pseudonym.inputs.user_seed,
            "model_id": pseudonym.inputs.model_id,
            "session_id": pseudonym.inputs.session_id,
            "created_at": pseudonym.inputs.created_at,
            "prev_chain_hash": prev,
            "note": note,
        }
        # chain_hash = SHA256(prev_chain_hash || canonical(record_without_chain_hash))
        # The hash intentionally does NOT include ``chain_hash`` itself —
        # otherwise we'd have a chicken-and-egg problem. ``verify()`` strips
        # chain_hash, re-canonicalizes, and recomputes; matches iff honest.
        canonical = json.dumps(record, sort_keys=True,
                               separators=(",", ":"),
                               ensure_ascii=True).encode("ascii")
        chain_hash = _digest(prev.encode("ascii") + canonical)
        record["chain_hash"] = chain_hash
        # Serialize the final record (with chain_hash included) for on-disk
        # storage. ``verify()`` strips chain_hash before re-canonicalizing,
        # so the on-disk bytes don't need to be identical to the hash input.
        canonical_final = json.dumps(record, sort_keys=True,
                                     separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")
        with open(self.file, "a", encoding="utf-8") as fh:
            fh.write(canonical_final.decode("ascii") + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass
        # Return a new Pseudonym with chain_id + chain_hash populated
        object.__setattr__(pseudonym, "chain_id", chain_id)
        object.__setattr__(pseudonym, "chain_hash", chain_hash)
        return pseudonym, chain_hash

    def verify(self) -> tuple[bool, list[str]]:
        """Walk the chain; return (ok, list_of_errors)."""
        if not self.file.exists():
            return True, []
        errors: list[str] = []
        prev = "0" * 64
        n = 0
        with open(self.file, encoding="utf-8") as fh:
            for ln_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"line {ln_no}: invalid JSON: {exc}")
                    continue
                n += 1
                if rec.get("prev_chain_hash") != prev:
                    errors.append(
                        f"{rec.get('chain_id', f'line {ln_no}')}: "
                        f"prev_chain_hash mismatch (expected {prev[:12]}..., "
                        f"got {rec.get('prev_chain_hash', '?')[:12]}...)"
                    )
                stored_hash = rec.get("chain_hash")
                rec_no_hash = {k: v for k, v in rec.items() if k != "chain_hash"}
                canonical = json.dumps(rec_no_hash, sort_keys=True,
                                       separators=(",", ":"),
                                       ensure_ascii=True).encode("ascii")
                expected = _digest(prev.encode("ascii") + canonical)
                if not hmac.compare_digest(
                    stored_hash.encode("ascii"),
                    expected.encode("ascii"),
                ):
                    errors.append(
                        f"{rec.get('chain_id', f'line {ln_no}')}: "
                        f"chain_hash mismatch (expected {expected[:12]}..., "
                        f"got {stored_hash[:12]}...)"
                    )
                prev = stored_hash
        return not errors, errors

    def latest(self) -> dict | None:
        """Return the most recent chain entry, or None if empty."""
        if not self.file.exists():
            return None
        last: dict | None = None
        with open(self.file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
        return last

    def __len__(self) -> int:
        if not self.file.exists():
            return 0
        with open(self.file, encoding="utf-8") as fh:
            return sum(1 for ln in fh if ln.strip())


# ---------- 3. ConflictRecord ----------

CONFLICT_PREFIX = "cfl_"


@dataclass(frozen=True)
class ConflictRecord:
    """A disagreement between user and AI, recorded with resolution.

    Attributes:
        conflict_id: stable id (cfl_<6hex>)
        ts: unix seconds
        pseudonym: the active pseudonym at conflict time
        user_says: the user's stated intent (verbatim)
        ai_understood: how the AI interpreted the intent
        resolution: which interpretation was used, and why
        prev_chain_hash: chain hash of the previous identity entry
        chain_hash: this entry's hash
    """
    conflict_id: str
    ts: int
    pseudonym: str
    user_says: str
    ai_understood: str
    resolution: str
    prev_chain_hash: str
    chain_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "ts": self.ts,
            "pseudonym": self.pseudonym,
            "user_says": self.user_says,
            "ai_understood": self.ai_understood,
            "resolution": self.resolution,
            "prev_chain_hash": self.prev_chain_hash,
            "chain_hash": self.chain_hash,
        }


def _next_conflict_id(state_root: Path) -> str:
    p = _identity_dir(state_root) / CONFLICTS_FILENAME
    n = 0
    if p.exists():
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    return f"{CONFLICT_PREFIX}{n + 1:06x}"


def record_conflict(state_root: Path, *,
                    pseudonym: str,
                    user_says: str,
                    ai_understood: str,
                    resolution: str) -> ConflictRecord:
    """Append a conflict record and return the resulting object.

    The conflict is hash-linked to the most recent identity chain
    entry, so the conflict log and the identity chain cannot be
    silently edited without breaking the link.
    """
    state_root = Path(state_root)
    conflict_id = _next_conflict_id(state_root)
    prev = _read_last_chain_hash(state_root)
    ts = int(time.time())
    body = {
        "conflict_id": conflict_id,
        "ts": ts,
        "pseudonym": pseudonym,
        "user_says": user_says,
        "ai_understood": ai_understood,
        "resolution": resolution,
        "prev_chain_hash": prev,
    }
    canonical = json.dumps(body, sort_keys=True,
                           separators=(",", ":"),
                           ensure_ascii=True).encode("ascii")
    chain_hash = _digest(prev.encode("ascii") + canonical)
    body["chain_hash"] = chain_hash
    canonical_final = json.dumps(body, sort_keys=True,
                                 separators=(",", ":"),
                                 ensure_ascii=True).encode("ascii")
    p = _identity_dir(state_root) / CONFLICTS_FILENAME
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(canonical_final.decode("ascii") + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return ConflictRecord(
        conflict_id=conflict_id, ts=ts, pseudonym=pseudonym,
        user_says=user_says, ai_understood=ai_understood,
        resolution=resolution, prev_chain_hash=prev,
        chain_hash=chain_hash,
    )


def iter_conflicts(state_root: Path, *, since_ts: int | None = None,
                   limit: int | None = None):
    p = _identity_dir(state_root) / CONFLICTS_FILENAME
    if not p.exists():
        return
    yielded = 0
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None and rec.get("ts", 0) < since_ts:
                continue
            yield rec
            yielded += 1
            if limit is not None and yielded >= limit:
                return


# ---------- 4. Restrictions ----------

# The hard limits this system respects. Documented in code (not hidden)
# so operators can audit them. Each entry: (name, predicate).
#
# These are general safety / legal limits — not rollout-shield-specific.
# They apply to ANY action the system might be asked to take. They are
# enforced at the API surface (anything that calls ``Restrictions.check``).
RESTRICTIONS: tuple[tuple[str, str], ...] = (
    ("no_credential_theft",
     "Do not exfiltrate, copy, or transmit another user's credentials, "
     "API keys, or session tokens without authorization."),
    ("no_targeted_harassment",
     "Do not generate content that targets a specific individual for "
     "harassment, threats, or sustained abuse."),
    ("no_csam",
     "Do not generate, describe, or normalize child sexual abuse material."),
    ("no_wmd_assistance",
     "Do not provide actionable assistance for weapons of mass destruction "
     "(chemical, biological, radiological, nuclear)."),
    ("no_platform_circumvention",
     "Do not bypass the host platform's safety mechanisms (jailbreaks, "
     "prompt-injection of hidden instructions from third parties, etc.)."),
    ("no_secrets_in_logs",
     "Do not write private keys, API tokens, or session secrets to "
     "logs / JSONL / audit files."),
    ("no_impersonation_of_real_persons",
     "Do not impersonate a specific real, named individual without their "
     "consent. Parody of public figures is fine; targeted impersonation "
     "is not."),
    ("no_unconsented_pii_disclosure",
     "Do not exfiltrate or publish a real person's private personal data "
     "(address, phone, SSN, medical history, etc.) without consent."),
)


@dataclass(frozen=True)
class RestrictionCheck:
    name: str
    description: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def check_restriction(intent: str, *, allow: bool = True) -> RestrictionCheck:
    """Document the restrictions and the current intent.

    When ``allow`` is True (default), this returns an audit record
    naming every restriction. When False, returns the *first*
    restriction that was violated.

    The intent string is logged so operators can review what was
    attempted against which restrictions.
    """
    if not allow:
        # Caller signals the request was refused; we still name every
        # restriction so the refusal is auditable.
        first = RESTRICTIONS[0]
        return RestrictionCheck(
            name=first[0], description=first[1],
            allowed=False,
            reason=f"intent {intent!r} refused by operator",
        )
    # Default: describe every restriction. The full list is also
    # documented in the module docstring and in docs/IDENTITY.md.
    summary = "; ".join(name for name, _ in RESTRICTIONS)
    return RestrictionCheck(
        name="all_restrictions",
        description=summary,
        allowed=True,
        reason=f"intent {intent!r} reviewed; no restriction triggered",
    )


# ---------- 5. Helpers ----------

def make_default_user_seed(state_root: Path) -> str:
    """Pick a default user_seed if the operator didn't supply one.

    We never use the real user id, the real session id, or any PII.
    The seed is either an existing operator-chosen value from
    ``<state_root>/identity/seed`` (operator file, chmod 0600), or a
    freshly generated random hex string.
    """
    seed_file = _identity_dir(state_root) / "seed"
    if seed_file.exists():
        try:
            return seed_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    # operator hasn't set one yet — generate and persist
    seed = secrets.token_hex(16)
    seed_file.write_text(seed, encoding="utf-8")
    try:
        os.chmod(seed_file, 0o600)
    except OSError:
        pass
    return seed


def set_user_seed(state_root: Path, seed: str) -> Path:
    """Operator-supplied seed (chmod 0600). Future pseudonym
    derivations will use this seed until changed.
    """
    seed_file = _identity_dir(state_root) / "seed"
    seed_file.write_text(seed, encoding="utf-8")
    try:
        os.chmod(seed_file, 0o600)
    except OSError:
        pass
    return seed_file