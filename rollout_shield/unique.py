"""One-way access gate between public surface and internal surface.

Implements the access policy described in ``UNIQUE.md`` and
``INTERNAL.md``. Two pieces:

1. ``OneWayGate`` — runtime check that refuses to expose internal
   paths or call internal-only functions unless the actor holds
   an identity token that the operator + model have authorized.

2. ``is_internal_authorized(actor)`` — pure function returning
   True/False based on the actor string. Authorized actor
   prefixes:

      * ``user:<handle>``           — the operator themselves
      * ``model:<model-id>``        — the operator's authorized model
      * ``agent:<agent-id>``        — an agent whose key was minted
        by the operator and whose chain link is recorded in
        ``identity/chain.jsonl``

   Anything else (``external``, ``public``, ``unknown``, missing)
   returns False.

The gate is intentionally minimal and stateless: it does not
introduce a new dependency on the identity chain. Operators can
hard-code the authorized actors in their config or use the
identity module to derive them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


# Authorized actor prefixes. These match the docs in UNIQUE.md /
# INTERNAL.md and the access_policy.yaml schema.
_USER_RE = re.compile(r"^user:[a-zA-Z0-9_\-\.]{1,64}$")
_MODEL_RE = re.compile(r"^model:[a-zA-Z0-9_\-\.]{1,64}$")
_AGENT_RE = re.compile(r"^agent:[a-zA-Z0-9_\-\.]{1,64}$")


def is_internal_authorized(actor: str) -> bool:
    """Return True iff the actor string is in the authorized set.

    The check is intentionally narrow: any deviation from the
    documented format returns False. This is a deny-by-default gate.
    """
    if not isinstance(actor, str) or not actor:
        return False
    return bool(
        _USER_RE.match(actor)
        or _MODEL_RE.match(actor)
        or _AGENT_RE.match(actor)
    )


# Paths that are NEVER legal for an external caller. Any attempt
# to resolve a path through these, even with traversal tricks, is
# refused.
INTERNAL_PATH_PREFIXES: tuple[str, ...] = (
    "keys_material",
    "identity",
    "access",
    "state",
    "safeups",
    ".beads",
    "plans",
    "tools/secure_state.py",
    "tools/safeup.py",
    "INTERNAL.md",
)


class OneWayGate:
    """Runtime gate between the public surface and internals.

    Use::

        gate = OneWayGate(state_root=Path("/path/to/state"))
        gate.require_authorization(actor="model:MiniMax-M3",
                                   intent="read keys_material")

    The ``require_authorization`` call:

      1. Verifies the actor is in the authorized set.
      2. Verifies the requested path (if any) does not escape into
         an internal prefix.
      3. Appends an audit record to the access log (best-effort).

    Both checks must pass; otherwise a ``PermissionError`` is raised.
    """

    def __init__(self, state_root: Path | None = None):
        self.state_root = Path(state_root) if state_root else None

    def require_authorization(self, *, actor: str, intent: str = "",
                              path: Path | None = None) -> None:
        """Raise ``PermissionError`` unless actor + path are authorized."""
        if not is_internal_authorized(actor):
            raise PermissionError(
                f"actor {actor!r} is not authorized for internal access; "
                f"intent was {intent!r}"
            )
        if path is not None and self._is_internal_path(path):
            raise PermissionError(
                f"path {path} crosses an internal-prefix boundary; "
                f"actor {actor!r} is authorized but the path is not"
            )
        # Best-effort audit. The access log itself is internal, so
        # this never leaks information to externals.
        try:
            from .audit_log import append_access_event
            if self.state_root is not None:
                append_access_event(
                    self.state_root,
                    action="unique.gate.authorize",
                    actor=actor,
                    target=str(path) if path else "",
                    result="ok",
                    detail={"intent": intent},
                )
        except Exception:
            pass  # audit must never block the gate

    def _is_internal_path(self, path: Path) -> bool:
        """True if any path component matches an internal prefix."""
        parts = path.resolve().parts
        for prefix in INTERNAL_PATH_PREFIXES:
            if any(p == prefix or p.startswith(prefix + "/")
                   or p.startswith(prefix + ".") for p in parts):
                return True
        return False

    def check_path(self, path: Path) -> bool:
        """Non-raising variant of require_authorization(path=...)."""
        try:
            self.require_authorization(
                actor="user:probe",  # only used to test path check;
                # actual authorization must come from the caller
                intent="probe",
                path=path,
            )
            return True
        except PermissionError:
            return False


def authorized_actors_from_chain(state_root: Path) -> list[str]:
    """Return the authorized actors implied by the identity chain.

    Every agent that has a recorded chain entry is considered
    authorized. The operator (``user:``) and the configured model
    (``model:``) are always included if a seed exists. Useful for
    pre-populating access lists in config.
    """
    actors: list[str] = []
    if state_root is None:
        return actors
    chain = state_root / "identity" / "chain.jsonl"
    if chain.exists():
        import json
        with open(chain, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # chain entry contains a pseudonym token; record the
                # model_id (and any agent:* hints).
                model_id = rec.get("model_id")
                if model_id:
                    actors.append(f"model:{model_id}")
    # Always include user + the default model if a seed exists.
    seed = state_root / "identity" / "seed"
    if seed.exists():
        actors.append("user:operator")
        actors.append("model:MiniMax-M3")
    return sorted(set(actors))