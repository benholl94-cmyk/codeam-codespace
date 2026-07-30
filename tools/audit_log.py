#!/usr/bin/env python3
"""audit_log.py — hash-chained, append-only audit log for the owner.

Every action that touches state, configs, or sensitive data is appended
to ``.audit/audit.jsonl``. Each entry includes the SHA-256 of the prior
entry, so any tampering invalidates the chain.

The audit log is **for the owner's eyes** — it records what Claude did
on the owner's behalf, when, and with what effect. The owner reads it
from their hardware (smartphone, laptop, etc.) via ``tools/owner_log.py``.

Design:
  * Append-only — there is no edit/delete API. The tool only appends.
  * Hash-chained — entry N includes sha256(entry N-1) in its ``prev`` field.
  * Self-verifying — ``verify`` walks the chain and refuses if any hash mismatches.
  * Local-only — never uploaded, never synced, gitignored.
  * Owner-readable — the viewer requires no key (the log itself is meant
    to be visible to the owner). What the log describes (e.g. private
    state) is encrypted separately via ``tools/secure_state.py``.

Entry schema::

    {
      "ts":     "2026-07-30T07:50:00Z",   # ISO 8601 UTC
      "actor":  "claude|owner|system",     # who triggered
      "action": "snapshot|verify|commit|encrypt|...",
      "target": "tools/safeup.py",          # what was touched
      "ok":     true|false,
      "detail": {"any": "context"},         # freeform but bounded
      "prev":   "<hex sha256 of prior entry, or null>"
    }
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

AUDIT_DIR = Path(os.environ.get("ROLLOUT_SHIELD_AUDIT", ".audit"))
LOG_PATH = AUDIT_DIR / "audit.jsonl"
GENESIS_PREV = "0" * 64  # sha256 of empty string


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _hash_entry(entry: dict) -> str:
    """Hash the canonical form of an entry (excluding 'hash' field)."""
    h = hashlib.sha256()
    h.update(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()


def _last_hash() -> str | None:
    """Read the last entry's hash from the log. Returns None if log empty."""
    if not LOG_PATH.exists():
        return None
    last = None
    with open(LOG_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                last = line
    if last is None:
        return None
    try:
        return json.loads(last).get("hash")
    except json.JSONDecodeError:
        return None


def append(action: str, *, actor: str = "claude", target: str = "",
           ok: bool = True, detail: dict | None = None) -> dict:
    """Append an entry to the audit log. Returns the entry written.

    This is the function other tools call (doctor.py, safeup.py, etc.).
    Designed to be cheap: O(1) IO (one append + one read of last line).
    """
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    # ensure audit dir is owner-only (defense in depth)
    try:
        os.chmod(AUDIT_DIR, 0o700)
    except OSError:
        pass
    entry = {
        "ts": _iso_now(),
        "actor": actor,
        "action": action,
        "target": target,
        "ok": ok,
        "detail": detail or {},
        "prev": _last_hash() or GENESIS_PREV,
    }
    entry["hash"] = _hash_entry(entry)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return entry


def verify() -> tuple[int, int, list[str]]:
    """Walk the chain. Returns (entries, ok_count, errors)."""
    if not LOG_PATH.exists():
        return 0, 0, []
    errors: list[str] = []
    prev = GENESIS_PREV
    ok = 0
    total = 0
    with open(LOG_PATH, "r", encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {ln}: invalid json ({exc})")
                continue
            total += 1
            if entry.get("prev") != prev:
                errors.append(
                    f"line {ln}: prev mismatch (expected {prev[:12]}…, "
                    f"got {entry.get('prev', '')[:12]}…)"
                )
                continue
            calc = _hash_entry({k: v for k, v in entry.items() if k != "hash"})
            if calc != entry.get("hash"):
                errors.append(
                    f"line {ln}: hash mismatch (expected {entry.get('hash', '')[:12]}…, "
                    f"got {calc[:12]}…)"
                )
                continue
            ok += 1
            prev = entry.get("hash")
    return total, ok, errors


def tail(n: int = 20) -> list[dict]:
    """Return the last N entries (newest last)."""
    if not LOG_PATH.exists():
        return []
    lines = [ln for ln in LOG_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def summarize(days: int = 7) -> dict:
    """Summarize activity in the last N days for the owner's review."""
    if not LOG_PATH.exists():
        return {"total": 0, "by_action": {}, "by_actor": {}, "failures": 0}
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=days)).timestamp()
    by_action: dict[str, int] = {}
    by_actor: dict[str, int] = {}
    failures = 0
    total = 0
    with open(LOG_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = _dt.datetime.fromisoformat(entry["ts"]).timestamp()
            except Exception:
                continue
            if ts < cutoff:
                continue
            total += 1
            by_action[entry.get("action", "?")] = by_action.get(entry.get("action", "?"), 0) + 1
            by_actor[entry.get("actor", "?")] = by_actor.get(entry.get("actor", "?"), 0) + 1
            if not entry.get("ok", True):
                failures += 1
    return {"total": total, "by_action": by_action,
            "by_actor": by_actor, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="audit_log", description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--append", action="store_true", help="append an entry")
    g.add_argument("--verify", action="store_true", help="verify hash chain")
    g.add_argument("--tail", type=int, metavar="N", help="show last N entries")
    g.add_argument("--summary", type=int, metavar="DAYS", nargs="?",
                   const=7, default=None, help="summarize last N days (default 7)")
    args = p.parse_args(argv)

    if args.append:
        action = input("action: ").strip() if not sys.stdin.isatty() else "manual"
        target = input("target: ").strip() if not sys.stdin.isatty() else ""
        e = append(action, actor="owner", target=target, ok=True)
        print(json.dumps(e, indent=2, sort_keys=True))
        return 0
    if args.verify:
        total, ok, errs = verify()
        if errs:
            print(f"AUDIT TAMPERED: {total - ok} of {total} entries invalid")
            for e in errs[:10]:
                print(f"  - {e}")
            return 2
        print(f"audit chain clean: {ok}/{total} entries verified")
        return 0
    if args.tail is not None:
        for entry in tail(args.tail):
            mark = "✓" if entry.get("ok") else "✗"
            print(f"  {mark} {entry.get('ts', '')}  "
                  f"{entry.get('actor', '?'):<6}  "
                  f"{entry.get('action', '?'):<16}  "
                  f"{entry.get('target', '')}")
        return 0
    if args.summary is not None:
        s = summarize(args.summary)
        print(json.dumps(s, indent=2, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
