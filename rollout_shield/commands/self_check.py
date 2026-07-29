"""Self-check: diagnose the rollout-shield environment."""

from __future__ import annotations

import argparse
import json
import platform
import sys

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

    # check we have at least one key
    keys = state.list_keys()
    info["checks"]["keys"] = {
        "ok": len(keys) > 0,
        "count": len(keys),
        "message": f"{len(keys)} key(s) registered" if keys else "no keys; run `rollout-shield keys new`",
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
