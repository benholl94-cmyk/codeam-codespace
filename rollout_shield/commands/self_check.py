"""Self-check: diagnose the rollout-shield environment."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from ..state import State


def cmd_self_check(state: State, args: argparse.Namespace) -> int:
    info = {
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "state": state.summary(),
        "checks": {},
    }

    # check cryptography availability
    try:
        import cryptography
        info["checks"]["cryptography"] = {
            "ok": True,
            "version": cryptography.__version__,
            "message": "cryptography available; signing and verifying will work",
        }
    except ImportError:
        info["checks"]["cryptography"] = {
            "ok": False,
            "message": "cryptography package missing; key generation and signing will fail. "
                       "Install with: pip install cryptography",
        }

    # check git availability (we use git refs in claim bodies sometimes)
    import shutil
    git_path = shutil.which("git")
    info["checks"]["git"] = {
        "ok": bool(git_path),
        "path": git_path or "(not found)",
        "message": "git found" if git_path else "git not in PATH",
    }

    # check key generation status.
    # A zero-key state is a normal initial condition, not a failure — the
    # `message` field surfaces the recommended action. Overall verdict still
    # reflects this check as ok=True so cold-install `self-check` passes.
    keys = state.list_keys()
    info["checks"]["keys"] = {
        "ok": True,
        "count": len(keys),
        "message": (
            f"{len(keys)} key(s) registered"
            if keys
            else "no keys yet; run `rollout-shield keys new` to create one"
        ),
    }

    # overall
    overall_ok = all(c.get("ok") for c in info["checks"].values())
    info["overall_ok"] = overall_ok

    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True, default=str))
        return 0 if overall_ok else 1

    print(f"python:        {info['python']['version']}  ({info['python']['platform']})")
    print(f"state root:    {info['state']['state_root']}")
    print(f"agents:        {info['state']['agents']['total']}")
    print(f"claims logged: {info['state']['claims_count']}")
    print(f"alerts logged: {info['state']['alerts_count']}")
    print()
    print("component checks:")
    for name, c in info["checks"].items():
        mark = "OK  " if c.get("ok") else "FAIL"
        print(f"  [{mark}] {name}: {c.get('message')}")
    print()
    if overall_ok:
        print("overall: OK")
        return 0
    print("overall: FAIL")
    return 1
