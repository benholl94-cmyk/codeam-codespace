#!/usr/bin/env python3
"""integrity.py — tool self-hash for tamper detection.

Each tool imports `verify_self_or_die()` at startup. This computes the
SHA-256 of the calling script and compares against a baseline stored in
``.audit/baseline/<script>.sha256``. Mismatch → exit with a refusal.

Workflow:
  1. First run: baseline is auto-created (legitimate install)
  2. Subsequent runs: hash is checked; mismatch aborts

To re-baseline after a legitimate code change:
  python3 tools/integrity.py --rebaseline

This catches Q3 from SELF_DIAGNOSIS (modified `_audit()` slipping past
the audit chain). The baseline lives in `.audit/baseline/` which is
mode 0700 and gitignored — the attacker would need both file edit AND
baseline write access to bypass.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import os
import sys
from pathlib import Path

BASELINE_DIR = Path(os.environ.get("ROLLOUT_SHIELD_BASELINE",
                                   ".audit/baseline"))


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _baseline_path(script_name: str) -> Path:
    return BASELINE_DIR / f"{script_name}.sha256"


def verify_self_or_die(script_name: str | None = None,
                       allow_create: bool = True) -> bool:
    """Check the caller's script against the baseline. Exit on mismatch.

    `script_name`: override the script name (default: caller filename).
    `allow_create`: if True and no baseline exists, create one and pass.
                    If False and no baseline exists, refuse (strict mode).
    """
    if script_name is None:
        # walk the stack to find the caller's __file__
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            return True  # can't verify, don't block
        caller_file = frame.f_back.f_globals.get("__file__")
        if not caller_file:
            return True
        caller_path = Path(caller_file)
        script_name = caller_path.name
        script_path = caller_path
    else:
        # resolve from sys.argv[0] / cwd
        script_path = Path(script_name)
        if not script_path.is_absolute():
            script_path = Path.cwd() / script_path
        script_name = script_path.name

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(BASELINE_DIR, 0o700)
    except OSError:
        pass

    baseline = _baseline_path(script_name)
    current = _hash_file(script_path)

    if not baseline.exists():
        if not allow_create:
            print(f"integrity: no baseline for {script_name} and strict mode on",
                  file=sys.stderr)
            sys.exit(2)
        baseline.write_text(current + "\n")
        return True

    expected = baseline.read_text().strip()
    if expected != current:
        print(f"INTEGRITY FAIL: {script_name} has been modified", file=sys.stderr)
        print(f"  expected: {expected[:16]}…", file=sys.stderr)
        print(f"  actual:   {current[:16]}…", file=sys.stderr)
        print(f"  to re-baseline: python3 tools/integrity.py --rebaseline",
              file=sys.stderr)
        sys.exit(3)
    return True


def cmd_rebaseline(args: argparse.Namespace) -> int:
    """Recompute baselines for one or more scripts (default: all tools)."""
    tools_dir = Path(__file__).resolve().parent
    if args.targets:
        targets = [Path(t) for t in args.targets]
    else:
        # all .py and .sh files in tools/
        targets = sorted(tools_dir.glob("*.py")) + sorted(tools_dir.glob("*.sh"))
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BASELINE_DIR, 0o700)
    for t in targets:
        h = _hash_file(t)
        baseline = _baseline_path(t.name)
        baseline.write_text(h + "\n")
        print(f"  {t.name}: {h[:16]}…")
    print(f"rebaselined {len(targets)} script(s) → {BASELINE_DIR}/")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify all baselines match current file hashes."""
    if not BASELINE_DIR.exists():
        print(f"no baselines at {BASELINE_DIR}/; run --rebaseline first")
        return 1
    tools_dir = Path(__file__).resolve().parent
    bad = 0
    total = 0
    for baseline in sorted(BASELINE_DIR.glob("*.sha256")):
        name = baseline.name.removesuffix(".sha256")
        script = tools_dir / name
        if not script.exists():
            print(f"  [MISS] {name}: baseline exists but script missing")
            bad += 1
            continue
        total += 1
        expected = baseline.read_text().strip()
        actual = _hash_file(script)
        if expected != actual:
            print(f"  [TAMPERED] {name}: {expected[:12]}… vs {actual[:12]}…")
            bad += 1
    if bad:
        print(f"FAIL: {bad}/{total} tool(s) tampered")
        return 2
    print(f"OK: {total} tool(s) integrity verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="integrity", description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--rebaseline", action="store_true",
                   help="compute and store baselines for all tools")
    g.add_argument("--verify", action="store_true",
                   help="verify all baselines match current hashes")
    g.add_argument("--check-self", action="store_true",
                   help="verify the caller's integrity (used internally)")
    args = p.parse_args(argv)
    if args.rebaseline:
        ns = argparse.Namespace(targets=None)
        return cmd_rebaseline(ns)
    if args.verify:
        return cmd_verify(args)
    if args.check_self:
        verify_self_or_die()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
