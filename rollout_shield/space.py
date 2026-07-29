"""Controller-policy enforcement for rollout-shield.

The ``controller_policy`` field on the state config declares which keys
are permitted to act in the current rollout space. Three policies are
defined:

- ``shared`` (default) — both human and device/hardware-anchored keys
  may sign claims. Used for development and shared spaces.
- ``device-only`` — ONLY keys with ``hardware_anchored=True`` may sign
  claims. Human keys are quarantined. Used for production App-controlled
  Spaces where the device is the sole authority.
- ``human-only`` — ONLY non-hardware-anchored keys may sign. Used for
  dev/test spaces where the device identity is intentionally excluded.

The policy is enforced at:

- key registration (``rollout-shield keys new``)
- claim creation (``rollout-shield claim create``)
- monitor + self-heal cycles (via :mod:`health_checks`)
- self-heal repairs (via :mod:`commands.self_heal`)

The controller policy is **advisory at the protocol level** — anyone
holding a private key can still sign a claim. The enforcement happens
at the local CLI: ``cmd_create`` in ``commands/claim.py`` consults
``enforce_policy`` before invoking the signer. This matches the
rollout-shield model: the runtime is a *tool*; the protocol is the
*truth*.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .state import State


VALID_POLICIES = ("shared", "device-only", "human-only")
DEFAULT_POLICY = "shared"


class PolicyViolation(Exception):
    """Raised when an action would violate the controller policy."""

    def __init__(self, policy: str, action: str, reason: str):
        self.policy = policy
        self.action = action
        self.reason = reason
        super().__init__(
            f"policy violation: policy={policy} action={action} reason={reason}"
        )


@dataclass
class PolicyInfo:
    """Summary of the current controller policy and key state."""
    policy: str
    total_keys: int
    device_keys: int
    human_keys: int
    quarantined_keys: int
    last_claim_signer: str | None
    last_claim_hardware_anchored: bool | None
    # violations: list of (severity, action, reason)
    violations: list[tuple[str, str, str]]


def load_policy(state: State) -> str:
    """Read the current controller policy from the state config."""
    cfg = state.load_config()
    policy = cfg.get("controller_policy", DEFAULT_POLICY)
    if policy not in VALID_POLICIES:
        # silently fall back to default rather than crash on a corrupt config
        return DEFAULT_POLICY
    return policy


def save_policy(state: State, policy: str, *, backup: bool = True) -> Path | None:
    """Persist the controller policy. Optionally back up the prior config."""
    if policy not in VALID_POLICIES:
        raise ValueError(f"unknown policy: {policy}; choices: {VALID_POLICIES}")
    cfg = state.load_config()
    backup_path: Path | None = None
    if backup and state.config_path.exists():
        backup_path = state.config_path.with_suffix(
            f".json.bak.{int(time.time())}"
        )
        backup_path.write_text(state.config_path.read_text(encoding="utf-8"),
                               encoding="utf-8")
    cfg["controller_policy"] = policy
    cfg["controller_policy_set_at"] = int(time.time())
    state.save_config(cfg)
    return backup_path


def is_hardware_anchored(key_meta: dict) -> bool:
    """Return True if the key metadata declares hardware anchoring."""
    return bool(key_meta.get("hardware_anchored"))


def latest_key_for_agent(state: State, agent_id: str) -> dict | None:
    """Find the most-recent key for an agent (newest by created_at)."""
    keys = [k for k in state.list_keys() if k.get("agent_id") == agent_id]
    if not keys:
        return None
    keys.sort(key=lambda k: k.get("created_at", 0), reverse=True)
    return keys[0]


def check_key_allowed(policy: str, key_meta: dict) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a given key under the policy."""
    if policy == "shared":
        return True, ""
    if key_meta.get("quarantined"):
        return False, f"key is quarantined: {key_meta.get('quarantine_reason', 'no reason')}"
    if policy == "device-only" and not is_hardware_anchored(key_meta):
        return False, "policy=device-only requires hardware_anchored=True"
    if policy == "human-only" and is_hardware_anchored(key_meta):
        return False, "policy=human-only forbids hardware-anchored keys"
    return True, ""


def enforce_policy_for_key(state: State, action: str, key_meta: dict) -> None:
    """Raise :class:`PolicyViolation` if the key is not allowed under the policy."""
    policy = load_policy(state)
    allowed, reason = check_key_allowed(policy, key_meta)
    if not allowed:
        raise PolicyViolation(policy, action, reason)


def validate_space(state: State) -> tuple[bool, list[tuple[str, str, str]]]:
    """Check the current state against the policy.

    Returns ``(consistent, violations)`` where ``violations`` is a list
    of ``(severity, action, reason)`` tuples. ``consistent`` is True when
    no violation is found.
    """
    policy = load_policy(state)
    violations: list[tuple[str, str, str]] = []

    # 1. Any non-policy compliant key in the registry is a violation.
    for k in state.list_keys():
        if k.get("quarantined"):
            continue
        allowed, reason = check_key_allowed(policy, k)
        if not allowed:
            violations.append(("error", "key_registry",
                               f"key {k.get('id')}: {reason}"))

    # 2. Any claim whose signer is now policy-violating is a violation.
    for c in state.iter_claims(limit=100000):
        agent_id = c.get("agent_id")
        if not agent_id:
            continue
        key = latest_key_for_agent(state, agent_id)
        if key is None:
            # the signer key has been removed — historical claim, not a
            # current-state violation
            continue
        allowed, reason = check_key_allowed(policy, key)
        if not allowed:
            violations.append(("warning", "claim_history",
                               f"claim {c.get('id')} from {agent_id}: {reason}"))

    consistent = not any(v[0] == "error" for v in violations)
    return consistent, violations


def quarantine_key(state: State, key_id: str, reason: str) -> bool:
    """Move a key out of the active registry (does NOT delete).

    The key metadata is updated with ``quarantined=True`` and
    ``quarantine_reason=reason``. A backup copy of the meta is written
    to ``keys/quarantine/`` so the operator can reverse the action.
    """
    meta = state.get_key(key_id)
    if meta is None:
        return False
    if meta.get("quarantined"):
        return True  # idempotent

    meta["quarantined"] = True
    meta["quarantine_reason"] = reason
    meta["quarantined_at"] = int(time.time())
    state.put_key(key_id, meta)

    # Optional: keep a backup under keys/quarantine/
    qdir = state.root / "keys" / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    qpath = qdir / f"{key_id}.json"
    qpath.write_text(json.dumps(meta, indent=2, sort_keys=True),
                      encoding="utf-8")
    return True


def unquarantine_key(state: State, key_id: str) -> bool:
    """Reverse a :func:`quarantine_key` action."""
    meta = state.get_key(key_id)
    if meta is None:
        return False
    if not meta.get("quarantined"):
        return True
    meta.pop("quarantined", None)
    meta.pop("quarantine_reason", None)
    meta.pop("quarantined_at", None)
    state.put_key(key_id, meta)
    return True


def space_info(state: State) -> PolicyInfo:
    """Build a :class:`PolicyInfo` summary from the current state."""
    policy = load_policy(state)
    keys = state.list_keys()
    device = sum(1 for k in keys if is_hardware_anchored(k))
    quarantined = sum(1 for k in keys if k.get("quarantined"))
    human = sum(1 for k in keys
                if not is_hardware_anchored(k) and not k.get("quarantined"))

    # find the most recent claim (regardless of agent)
    last_signer: str | None = None
    last_anchored: bool | None = None
    last_claim: dict | None = None
    for c in state.iter_claims(limit=100000):
        if c.get("ts", 0) > (last_claim or {}).get("ts", -1):
            last_claim = c
    if last_claim:
        last_signer = last_claim.get("agent_id")
        key = latest_key_for_agent(state, last_signer) if last_signer else None
        if key:
            last_anchored = is_hardware_anchored(key)

    _, violations = validate_space(state)
    return PolicyInfo(
        policy=policy,
        total_keys=len(keys),
        device_keys=device,
        human_keys=human,
        quarantined_keys=quarantined,
        last_claim_signer=last_signer,
        last_claim_hardware_anchored=last_anchored,
        violations=violations,
    )
