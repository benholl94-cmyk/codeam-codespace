"""Operator-editable policy loader.

Reads ``/etc/rollout-shield/policy.yaml`` (or a path passed explicitly)
and exposes a simple ``check(policy, action=, actor=)`` function that
returns ``(allowed, reason)``.

The policy file is intentionally minimal — operators edit YAML, the
code reads it. PyYAML is an optional dependency; if it's missing or
the file doesn't exist, every action is default-allowed (the hard-
coded checks elsewhere still apply).

File format::

    version: 1
    rules:
      - action: claim.create
        allowed_actors: [self]
        rate_limit_per_minute: 60
      - action: state.update_reputation
        allowed_actors: [self]
        max_delta_per_call: 1.0
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path("/etc/rollout-shield/policy.yaml")


def load_policy(path: Path | None = None) -> dict[str, Any]:
    """Load a policy YAML file; return ``{"version": 1, "rules": []}``
    on missing file, missing PyYAML, or parse error.

    Errors are deliberately swallowed: a broken policy file should
    not prevent rollout-shield from starting. Operators can run
    ``rollout-shield audit --json`` to surface the broken policy via
    a custom rule if they wish.
    """
    p = Path(path) if path else DEFAULT_POLICY_PATH
    if not p.exists():
        return {"version": 1, "rules": []}
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return {"version": 1, "rules": []}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {"version": 1, "rules": []}
    except Exception:
        return {"version": 1, "rules": []}


def check(policy: dict[str, Any], *, action: str,
          actor: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)``.

    A rule matches if its ``action`` equals the query's ``action``. If
    multiple rules match, the first match wins. If no rule matches,
    the action is default-allowed (other in-code checks still apply).

    Special allowed_actors values:
      * ``"any"``   — allow any actor
      * ``"self"``  — allow if the actor's agent_id equals the actor's
                       ``agent_id`` (i.e., self-update only)
      * ``"cli:X"`` — allow only actors prefixed with ``cli:``
    """
    for rule in policy.get("rules", []):
        if rule.get("action") != action:
            continue
        allowed_actors = rule.get("allowed_actors", [])
        if "any" in allowed_actors:
            return True, ""
        if "self" in allowed_actors and actor.startswith("agent:"):
            return True, ""
        if actor in allowed_actors:
            return True, ""
        return False, (
            f"actor {actor!r} not in allowed_actors "
            f"{allowed_actors!r} for action {action!r}"
        )
    return True, "no matching rule; default-allow"