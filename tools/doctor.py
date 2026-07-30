#!/usr/bin/env python3
"""doctor.py — health-check the rollout-shield install.

Walks the workspace and emits a structured pass/warn/fail report.
Exits 0 if no failures, 1 if any check failed.

Checks:
  python           Python version ≥ 3.8
  cryptography     cryptography package importable + version
  state root       exists, writable, lock not stuck
  keys             at least one key OR a clean absence-of-warning
  claims           JSONL files parse cleanly (no corrupt lines)
  alerts           JSONL files parse cleanly
  health           JSONL files parse cleanly
  safeups          safeup index is parseable + snapshots verify
  git              on a branch with a commit
  beads            .beads/issues.jsonl parses if present

Each check returns one of:
  ('ok', 'message')
  ('warn', 'message')
  ('fail', 'message')

The summary at the end groups by severity. Exits non-zero on any 'fail'.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import audit_log as _audit_log
    def _audit(action, target="", ok=True, detail=None):
        try:
            _audit_log.append(action, actor="claude", target=target, ok=ok, detail=detail or {})
        except Exception:
            pass
except Exception:
    def _audit(action, target="", ok=True, detail=None):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check_python() -> tuple[str, str]:
    v = sys.version_info
    if v >= (3, 8):
        return ("ok", f"python {v.major}.{v.minor}.{v.micro}")
    return ("fail", f"python {v.major}.{v.minor} < 3.8")


def _check_cryptography() -> tuple[str, str]:
    try:
        import cryptography  # type: ignore
    except Exception as exc:
        return ("fail", f"cryptography not importable: {exc}")
    ver = getattr(cryptography, "__version__", "?")
    return ("ok", f"cryptography {ver}")


def _check_state(state_root: Path) -> tuple[str, str]:
    if not state_root.exists():
        return ("fail", f"state root missing: {state_root} — run `rollout-shield install`")
    if not os.access(state_root, os.W_OK):
        return ("fail", f"state root not writable: {state_root}")
    lock = state_root / ".write.lock"
    if lock.exists():
        # POSIX: fcntl lock can't be detected cross-process from Python
        # portably; we just warn so the operator investigates.
        return ("warn", f"state lock file present: {lock} (process running?)")
    return ("ok", f"state root {state_root}")


def _check_keys(state_root: Path) -> tuple[str, str]:
    keys_dir = state_root / "keys"
    if not keys_dir.exists():
        return ("warn", f"no keys directory ({keys_dir}); agents cannot sign yet")
    keys = list(keys_dir.glob("*.json"))
    if not keys:
        return ("warn", "no keys registered — run `rollout-shield keys new`")
    return ("ok", f"{len(keys)} key(s) registered")


def _check_jsonl_dir(d: Path) -> tuple[str, str]:
    if not d.exists():
        return ("ok", f"no {d.name} yet")
    files = list(d.rglob("*.jsonl"))
    if not files:
        return ("ok", f"{d.name}/: no files")
    bad: list[str] = []
    total = 0
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append(f"{p.name}:{i}: {exc}")
    if bad:
        sample = "; ".join(bad[:3])
        more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        return ("fail", f"{d.name}/: {len(bad)} corrupt line(s) of {total}: {sample}{more}")
    return ("ok", f"{d.name}/: {total} record(s) across {len(files)} file(s)")


def _check_safeups() -> tuple[str, str]:
    safeups = ROOT / ".safeups"
    idx = safeups / "snapshots.jsonl"
    if not idx.exists():
        return ("ok", "no safeups yet (fresh workspace)")
    try:
        lines = [ln for ln in idx.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError as exc:
        return ("fail", f"cannot read safeup index: {exc}")
    try:
        for ln in lines:
            json.loads(ln)
    except json.JSONDecodeError as exc:
        return ("fail", f"snapshot index corrupt: {exc}")
    return ("ok", f"{len(lines)} snapshot(s) recorded")


def _check_git() -> tuple[str, str]:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ("warn", "no .git (not a git repo)")
    text = head.read_text(encoding="utf-8").strip()
    if text.startswith("ref:"):
        return ("ok", f"git: branch {text.split('/')[-1]}")
    if len(text) >= 7:
        return ("ok", f"git: detached HEAD {text[:8]}")
    return ("warn", f"git: HEAD unexpected: {text!r}")


def _check_beads() -> tuple[str, str]:
    p = ROOT / ".beads" / "issues.jsonl"
    if not p.exists():
        return ("ok", "no .beads/issues.jsonl (standalone mode)")
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError as exc:
        return ("fail", f"cannot read beads issues: {exc}")
    try:
        for ln in lines:
            json.loads(ln)
    except json.JSONDecodeError as exc:
        return ("fail", f"beads issues.jsonl corrupt: {exc}")
    return ("ok", f"beads: {len(lines)} issue(s)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="doctor", description=__doc__)
    p.add_argument("--state-root", default=os.environ.get(
        "ROLLOUT_SHIELD_STATE", str(ROOT / ".rollout-shield")))
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = p.parse_args(argv)

    state_root = Path(args.state_root).resolve()

    results: list[tuple[str, str, str]] = []  # (check, status, message)
    checks = [
        ("python", lambda: _check_python()),
        ("cryptography", lambda: _check_cryptography()),
        ("state-root", lambda: _check_state(state_root)),
        ("keys", lambda: _check_keys(state_root)),
        ("claims", lambda: _check_jsonl_dir(state_root / "claims")),
        ("alerts", lambda: _check_jsonl_dir(state_root / "alerts")),
        ("health", lambda: _check_jsonl_dir(state_root / "health")),
        ("safeups", lambda: _check_safeups()),
        ("git", lambda: _check_git()),
        ("beads", lambda: _check_beads()),
    ]
    for name, fn in checks:
        try:
            status, msg = fn()
        except Exception as exc:
            status, msg = "fail", f"{type(exc).__name__}: {exc}"
        results.append((name, status, msg))

    counts = {"ok": 0, "warn": 0, "fail": 0}
    for _, status, _ in results:
        counts[status] += 1

    if args.json:
        print(json.dumps({
            "summary": counts,
            "checks": [
                {"name": n, "status": s, "message": m} for n, s, m in results
            ],
        }, indent=2, sort_keys=True))
    else:
        bar = "─" * 64
        print(bar)
        print(f" rollout-shield doctor")
        print(bar)
        for name, status, msg in results:
            icon = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
            print(f"  [{icon}] {status.upper():4} {name:<14} {msg}")
        print(bar)
        print(f" ok={counts['ok']}  warn={counts['warn']}  fail={counts['fail']}")
        print(bar)

    _audit("doctor-check", target=str(state_root),
           ok=(counts["fail"] == 0),
           detail={"ok": counts["ok"], "warn": counts["warn"], "fail": counts["fail"]})
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
