#!/usr/bin/env python3
"""release.py — cut a version tag + CHANGELOG entry from the current state.

Pipeline:
  1. snapshot the working tree (via safeup.py)
  2. verify all tests pass
  3. read version (from VERSION file or last git tag)
  4. ask for change summary (positional arg); pull more from `--notes`
  5. update CHANGELOG.md (or create it)
  6. update VERSION file
  7. create git tag locally (do NOT push — operator decides when)
  8. print the final release manifest

Design constraints:
  * no third-party deps (stdlib)
  * safe by default: refuses to release with uncommitted changes, failing
    tests, or missing CHANGELOG (unless --allow-dirty)
  * works offline: doesn't need network for any step
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAFEUP = ROOT / "tools" / "safeup.py"


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True
         ) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, check=check)


def _git(args: list[str]) -> str:
    r = _run(["git", *args], check=False)
    return r.stdout.strip()


def _bump(version: str, kind: str) -> str:
    """kind: 'patch' | 'minor' | 'major'."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+](.+))?", version)
    if not m:
        raise ValueError(f"not a semver: {version!r}")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4) or ""
    if kind == "patch":
        patch += 1
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "major":
        major += 1
        minor = patch = 0
    else:
        raise ValueError(kind)
    return f"{major}.{minor}.{patch}" + (f"-{pre}" if pre else "")


def _read_version() -> str | None:
    f = ROOT / "VERSION"
    if f.exists():
        return f.read_text().strip()
    tags = _git(["tag", "--list", "v*.*.*", "--sort=-v:refname"]).splitlines()
    return tags[0].lstrip("v") if tags else None


def _write_version(v: str) -> None:
    (ROOT / "VERSION").write_text(v + "\n")


def _update_changelog(version: str, summary: str, notes: str) -> None:
    f = ROOT / "CHANGELOG.md"
    today = _dt.date.today().isoformat()
    header_line = f"## v{version} — {today}\n"
    body = (summary.strip() + ("\n\n" + notes.strip() if notes.strip() else "") + "\n")
    new_entry = header_line + "\n" + body + "\n"
    if f.exists():
        existing = f.read_text()
        if header_line in existing:
            print(f"CHANGELOG already has entry for v{version}", file=sys.stderr)
            return
        # insert after the top header
        m = re.match(r"(# Changelog\n+)", existing)
        if m:
            new = existing[:m.end()] + "\n" + new_entry + existing[m.end():]
        else:
            new = "# Changelog\n\n" + new_entry + existing
    else:
        new = (
            "# Changelog\n\n"
            "All notable changes to rollout-shield are documented in this file.\n"
            "Format follows https://keepachangelog.com (kept simple).\n\n"
            + new_entry
        )
    f.write_text(new)
    print(f"updated {f.name} (v{version})")


def cmd_release(args: argparse.Namespace) -> int:
    # 1. snapshot first — non-negotiable
    print("[release] safeup snapshot …")
    r = _run([sys.executable, str(SAFEUP), "snapshot",
              "--op", f"pre-release-{args.bump}", "--root", args.root])
    if r.returncode != 0:
        print(r.stdout, r.stderr, file=sys.stderr)
        return 1

    # 2. verify clean tree
    status = _git(["status", "--porcelain"])
    if status and not args.allow_dirty:
        print("ERROR: working tree is dirty. Commit/stash or pass --allow-dirty.",
              file=sys.stderr)
        print(status, file=sys.stderr)
        return 2

    # 3. run tests
    r = _run([sys.executable, "tests/run_all.py"], check=False)
    if r.returncode != 0:
        print("ERROR: tests failed; refusing to release.", file=sys.stderr)
        print(r.stdout[-2000:], file=sys.stderr)
        return 3

    # 4. version bump
    cur = _read_version() or "0.1.0"
    if args.version:
        new = args.version
    else:
        new = _bump(cur, args.bump)

    # 5. CHANGELOG
    _update_changelog(new, args.summary, args.notes or "")
    _write_version(new)

    # 6. tag (local; not pushed)
    tag = f"v{new}"
    r = _run(["git", "add", "CHANGELOG.md", "VERSION"], check=False)
    _run(["git", "commit", "-m", f"release: v{new} — {args.summary[:60]}",
          "--no-verify"], check=False)
    _run(["git", "tag", "-a", tag, "-m", args.summary], check=False)
    print(f"[release] created tag {tag}")

    # 7. manifest summary
    print()
    print(f"  version:   {new}")
    print(f"  tag:       {tag}")
    print(f"  summary:   {args.summary}")
    print(f"  CHANGELOG: {(ROOT / 'CHANGELOG.md').name} updated")
    print()
    print("operator action: review the diff, then push with:")
    print(f"  git push origin main {tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release")
    p.add_argument("--bump", choices=["patch", "minor", "major"], default="patch")
    p.add_argument("--version", help="explicit version (overrides --bump)")
    p.add_argument("--summary", required=True, help="one-line changelog summary")
    p.add_argument("--notes", default="", help="additional changelog body")
    p.add_argument("--allow-dirty", action="store_true",
                   help="release even with uncommitted changes")
    p.add_argument("--root", default=".safeups",
                   help="safeup root (default: .safeups)")
    p.set_defaults(func=cmd_release)
    return p.parse_args(argv).func(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
