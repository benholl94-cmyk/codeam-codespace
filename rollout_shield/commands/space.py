"""``rollout-shield space`` — inspect/manage the controller policy.

The space is a single declaration of authority: which keys are
permitted to sign claims in the current rollout space. Three policies
are supported:

- ``shared`` (default) — human + device keys both permitted
- ``device-only`` — ONLY hardware-anchored keys may sign
- ``human-only`` — ONLY non-hardware-anchored keys may sign

Subcommands:

- ``show``        — print the current policy + statistics
- ``set-policy``  — switch policy (backs up config)
- ``validate``    — enforce the policy against the current state
- ``quarantine``  — move a key out of the active registry
- ``unquarantine`` — reverse a quarantine
"""

from __future__ import annotations

import argparse
import json
import sys

from ..space import (
    VALID_POLICIES,
    load_policy,
    quarantine_key,
    save_policy,
    space_info,
    unquarantine_key,
    validate_space,
)
from ..state import State


def cmd_space(state: State, args: argparse.Namespace) -> int:
    sub = args.space_command or "show"
    if sub == "show":
        return _space_show(state, args)
    if sub == "set-policy":
        return _space_set_policy(state, args)
    if sub == "validate":
        return _space_validate(state, args)
    if sub == "quarantine":
        return _space_quarantine(state, args)
    if sub == "unquarantine":
        return _space_unquarantine(state, args)
    print(f"unknown space subcommand: {sub}", file=sys.stderr)
    return 2


def _space_show(state: State, args: argparse.Namespace) -> int:
    info = space_info(state)
    payload = {
        "policy": info.policy,
        "valid_policies": list(VALID_POLICIES),
        "keys": {
            "total": info.total_keys,
            "device": info.device_keys,
            "human": info.human_keys,
            "quarantined": info.quarantined_keys,
        },
        "last_claim": {
            "signer": info.last_claim_signer,
            "hardware_anchored": info.last_claim_hardware_anchored,
        } if info.last_claim_signer else None,
        "violations": [
            {"severity": s, "action": a, "reason": r}
            for s, a, r in info.violations
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"controller policy: {info.policy}")
    print(f"  valid policies:  {', '.join(VALID_POLICIES)}")
    print("  keys:")
    print(f"    total:        {info.total_keys}")
    print(f"    device:       {info.device_keys}")
    print(f"    human:        {info.human_keys}")
    print(f"    quarantined:  {info.quarantined_keys}")
    if info.last_claim_signer:
        anchored = "yes" if info.last_claim_hardware_anchored else "no"
        print(f"  last claim:     {info.last_claim_signer} (hardware_anchored={anchored})")
    if info.violations:
        print(f"  violations:     {len(info.violations)}")
        for v in info.violations:
            print(f"    [{v[0]}] {v[1]}: {v[2]}")
    else:
        print("  violations:     none")
    return 0


def _space_set_policy(state: State, args: argparse.Namespace) -> int:
    target = args.policy
    if target not in VALID_POLICIES:
        print(f"unknown policy: {target}; choices: {VALID_POLICIES}",
              file=sys.stderr)
        return 1
    current = load_policy(state)
    if current == target:
        print(f"policy already set to: {target}")
        return 0
    if not args.yes:
        print(f"current policy: {current}")
        print(f"target policy:  {target}")
        print()
        print("WARNING: changing the policy will affect future key registration + claim signing.")
        print(f"  - {target} will REJECT keys / claims by the wrong authority.")
        print("  - prior non-policy-compliant keys are not auto-quarantined; "
              "run `rollout-shield space validate` afterwards.")
        print()
        print("pass --yes to confirm.")
        return 1
    backup = save_policy(state, target, backup=True)
    if args.json:
        print(json.dumps({
            "previous": current,
            "current": target,
            "config_backup": str(backup) if backup else None,
        }, indent=2, sort_keys=True))
    else:
        print(f"policy: {current} → {target}")
        if backup:
            print(f"backup:  {backup}")
    return 0


def _space_validate(state: State, args: argparse.Namespace) -> int:
    consistent, violations = validate_space(state)
    payload = {
        "consistent": consistent,
        "violations": [{"severity": s, "action": a, "reason": r}
                       for s, a, r in violations],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if consistent:
            print("space: consistent with policy")
        else:
            print("space: INCONSISTENT with policy")
        for v in violations:
            print(f"  [{v[0]}] {v[1]}: {v[2]}")
    return 0 if consistent else 1


def _space_quarantine(state: State, args: argparse.Namespace) -> int:
    ok = quarantine_key(state, args.key_id, reason=args.reason or "manual quarantine")
    if not ok:
        print(f"no such key: {args.key_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"key_id": args.key_id, "quarantined": True}, indent=2))
    else:
        print(f"quarantined: {args.key_id}")
    return 0


def _space_unquarantine(state: State, args: argparse.Namespace) -> int:
    ok = unquarantine_key(state, args.key_id)
    if not ok:
        print(f"no such key: {args.key_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"key_id": args.key_id, "quarantined": False}, indent=2))
    else:
        print(f"unquarantined: {args.key_id}")
    return 0
