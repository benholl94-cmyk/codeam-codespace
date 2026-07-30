#!/usr/bin/env python3
"""owner_heart.py — owner-facing heartbeat entry & age-check (audit log).

SECURITY BOUNDS (audited 2026-07-30, SELF_DIAGNOSIS §D #3, §E #1):

  In scope:
    * write kind=heartbeat entry to .audit/audit.jsonl
    * report age-staleness against 24h/26h thresholds
    * emit machine-readable JSON for CI/cron consumers

  Out of scope (cannot be reached from this entry point):
    * actor spoofing — actor is hardcoded "system"
    * hash-chain relaxation — every entry is chained against prev
    * audit-dir perms — chmod 0700 enforced inside audit_log.append()
    * arbitrary action — append("heartbeat") only

  --force ONLY bypasses the 24h mtime idempotency gate. It does NOT
  relax any other invariant.

  Known gaps (filed as follow-up beads):
    G1: cross-process atomicity — two racing `heart` calls can both write
    G2: clock-skew — if host clock goes backward, mtime gate can
        spuriously pass on a wrapped entry
    G3: symlink-attack via $ROLLOUT_SHIELD_AUDIT — defense is operator
        hygiene: do not let attackers set the env var
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_log as _al  # noqa: E402

WARN_AFTER_SECONDS = 24 * 3600
FAIL_AFTER_SECONDS = 26 * 3600


def _emit(payload: dict, json_mode: bool) -> None:
    """Single output sink. JSON-mode is a strict parseable contract."""
    if json_mode:
        sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    else:
        for k, v in payload.items():
            sys.stdout.write(f"  {k}: {v}\n")


def cmd_heart(args: argparse.Namespace) -> int:
    entry = _al.heartbeat(force=args.force)
    if entry is None:
        payload = {
            "status": "already_beat",
            "within_seconds": 86400,
            "force_bypass_available": True,
        }
        _emit(payload, args.json)
        return 1
    payload = {
        "status": "ok",
        "action": "heartbeat",
        "forced": args.force,
        "entry": entry,
    }
    _emit(payload, args.json)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    age = _al.last_heartbeat_age_seconds()
    if age is None:
        log_has_entries = bool(_al.LOG_PATH.exists() and _al.tail(n=1))
        if log_has_entries:
            payload = {
                "status": "broken",
                "reason": "no_heartbeat_but_log_has_entries",
                "exit_code": 2,
                "age_seconds": None,
                "warn_threshold_hours": WARN_AFTER_SECONDS / 3600,
                "fail_threshold_hours": FAIL_AFTER_SECONDS / 3600,
            }
            _emit(payload, args.json)
            return 2
        payload = {
            "status": "initial",
            "reason": "empty_log",
            "exit_code": 0,
            "age_seconds": None,
            "warn_threshold_hours": WARN_AFTER_SECONDS / 3600,
            "fail_threshold_hours": FAIL_AFTER_SECONDS / 3600,
        }
        _emit(payload, args.json)
        return 0
    if age > FAIL_AFTER_SECONDS:
        status, exit_code = "broken", 2
    elif age > WARN_AFTER_SECONDS:
        status, exit_code = "stale", 1
    else:
        status, exit_code = "ok", 0
    payload = {
        "status": status,
        "exit_code": exit_code,
        "age_seconds": age,
        "age_hours": round(age / 3600.0, 4),
        "warn_threshold_hours": WARN_AFTER_SECONDS / 3600,
        "fail_threshold_hours": FAIL_AFTER_SECONDS / 3600,
    }
    _emit(payload, args.json)
    return exit_code


def cmd_tail(args: argparse.Namespace) -> int:
    entries = _al.tail(args.n)
    beats = [e for e in entries if e.get("action") == "heartbeat"]
    payload = {
        "count": len(beats),
        "entries": beats,
        "n_requested": args.n,
    }
    _emit(payload, args.json)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="owner_heart", description=__doc__)
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON output (for CI / cron)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("heart",
                       help="write today's heartbeat (idempotent within 24h)")
    s.add_argument("--force", action="store_true",
                   help="bypass 24h idempotency")

    sub.add_parser("check", help="age-check the last heartbeat")

    s = sub.add_parser("tail",
                       help="show recent heartbeat entries (default 20)")
    s.add_argument("n", type=int, nargs="?", default=20)

    args = p.parse_args(argv)
    return {
        "heart": cmd_heart,
        "check": cmd_check,
        "tail": cmd_tail,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
