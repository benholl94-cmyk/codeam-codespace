#!/usr/bin/env python3
"""owner_log.py — read-only view of the audit log for the owner.

Designed for the owner's hardware (smartphone, laptop, terminal):
  * No write access — read-only operations only
  * Multiple views: tail, summary, full chain, suspicious-activity filter
  * Works without any unlock file (the log itself is meant to be visible)
  * Optional JSON output for piping to other tools

Usage:
  python3 tools/owner_log.py tail 20
  python3 tools/owner_log.py summary 7
  python3 tools/owner_log.py verify
  python3 tools/owner_log.py grep snapshot
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Re-use audit_log internals
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_log as _al


def cmd_tail(args: argparse.Namespace) -> int:
    entries = _al.tail(args.n)
    if not entries:
        print("(audit log empty)")
        return 0
    for e in entries:
        mark = "✓" if e.get("ok") else "✗"
        print(f"  {mark} {e.get('ts', '')}  "
              f"{e.get('actor', '?'):<6}  "
              f"{e.get('action', '?'):<20}  "
              f"{e.get('target', '')}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    s = _al.summarize(args.days)
    print(f"audit summary — last {args.days} day(s):")
    print(f"  total entries:    {s['total']}")
    print(f"  failures:         {s['failures']}")
    print(f"  by actor:         {json.dumps(s['by_actor'], sort_keys=True)}")
    print(f"  by action:        {json.dumps(s['by_action'], sort_keys=True)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    total, ok, errs = _al.verify()
    if errs:
        print(f"⚠ TAMPER DETECTED: {total - ok} of {total} entries failed")
        for e in errs[:10]:
            print(f"  - {e}")
        return 2
    print(f"✓ chain clean: {ok}/{total} entries verified")
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    if not _al.LOG_PATH.exists():
        print("(audit log empty)")
        return 0
    matches = 0
    for line in _al.LOG_PATH.read_text(encoding="utf-8").splitlines():
        if args.pattern in line:
            print(line)
            matches += 1
    print(f"--- {matches} match(es) for {args.pattern!r}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show one entry by hash prefix or sequence number."""
    if not _al.LOG_PATH.exists():
        print("(audit log empty)")
        return 1
    target = args.id
    for ln, line in enumerate(_al.LOG_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if target == str(ln) or line.startswith("{" + json.dumps(target)):
            print(json.dumps(json.loads(line), indent=2, sort_keys=True))
            return 0
        if target in line:
            print(json.dumps(json.loads(line), indent=2, sort_keys=True))
            return 0
    print(f"no entry matching: {target}")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="owner_log", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("tail", help="show last N entries (default 20)")
    s.add_argument("n", type=int, nargs="?", default=20)

    s = sub.add_parser("summary", help="summarize last N days")
    s.add_argument("days", type=int, nargs="?", default=7)

    sub.add_parser("verify", help="walk hash chain, fail on tamper")

    s = sub.add_parser("grep", help="filter entries by substring")
    s.add_argument("pattern")

    s = sub.add_parser("show", help="show one entry by line# or hash prefix")
    s.add_argument("id")

    args = p.parse_args(argv)
    return {"tail": cmd_tail, "summary": cmd_summary, "verify": cmd_verify,
            "grep": cmd_grep, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
