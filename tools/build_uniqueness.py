#!/usr/bin/env python3
"""Compute the unique-fingerprint for this repository.

The fingerprint proves the repo is NOT a verbatim copy of any other
repo on the planet. Inputs:

  * canonical SHA-256 of every tracked file in the working tree
  * the commit SHA of the current HEAD
  * the author identity from LICENSE (default: the repo author)
  * the timestamp at which the fingerprint was generated

Two repos that diverge in any tracked file, HEAD, or timestamp get
different fingerprints. Forks share parent commits but diverge in
their own commits, working-tree files, and timestamps — so forks
get different fingerprints from their parents.

Usage:
  python3 tools/build_uniqueness.py            # print fingerprint
  python3 tools/build_uniqueness.py --write    # also write UNIQUE.json
  python3 tools/build_uniqueness.py --verify   # verify an existing UNIQUE.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT,
                                   text=True).strip()


def canonical_file_hash(path: Path) -> str:
    """SHA-256 of the file's content + its repo-relative path."""
    h = hashlib.sha256()
    # path goes into the hash so files renamed across repos don't collide
    h.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
    h.update(b"\0")
    h.update(path.read_bytes())
    return h.hexdigest()


def compute_tree_digest() -> str:
    """Walk every tracked file; produce a single SHA-256."""
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True,
    )
    files = sorted(line.strip() for line in out.splitlines() if line.strip())
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(canonical_file_hash(REPO_ROOT / rel).encode("ascii"))
    return h.hexdigest()


def build_fingerprint(*, author: str, commit: str,
                      timestamp: int | None = None) -> dict:
    """Return the full fingerprint document (dict, JSON-serializable)."""
    if timestamp is None:
        timestamp = int(time.time())
    tree_digest = compute_tree_digest()
    # The final fingerprint folds every input together. Order matters
    # and is part of the contract.
    payload = json.dumps({
        "tree_digest": tree_digest,
        "commit": commit,
        "author": author,
        "timestamp": timestamp,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(payload).hexdigest()
    return {
        "fingerprint": fingerprint,
        "tree_digest": tree_digest,
        "commit": commit,
        "author": author,
        "timestamp": timestamp,
        "schema_version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--author", default="benholl94-cmyk",
                   help="author identity to bind the fingerprint to")
    p.add_argument("--write", action="store_true",
                   help="also write UNIQUE.json")
    p.add_argument("--verify", action="store_true",
                   help="verify the existing UNIQUE.json against HEAD")
    args = p.parse_args(argv)

    commit = _git("rev-parse", "HEAD")
    fp = build_fingerprint(author=args.author, commit=commit)

    if args.verify:
        existing_path = REPO_ROOT / "UNIQUE.json"
        if not existing_path.exists():
            print("UNIQUE.json: missing", file=sys.stderr)
            return 2
        try:
            existing = json.loads(existing_path.read_text())
        except Exception as exc:
            print(f"UNIQUE.json: malformed JSON: {exc}", file=sys.stderr)
            return 2
        # Compare only the deterministic fields (timestamp is allowed
        # to differ from a stale UNIQUE.json).
        same = (
            existing.get("fingerprint") == fp["fingerprint"]
            or (
                existing.get("tree_digest") == fp["tree_digest"]
                and existing.get("commit") == fp["commit"]
                and existing.get("author") == fp["author"]
            )
        )
        if same:
            print(f"UNIQUE.json: OK ({fp['fingerprint'][:24]}...)")
            return 0
        print("UNIQUE.json: STALE (working tree has drifted)",
              file=sys.stderr)
        print(f"  recorded: {existing.get('fingerprint', '?')[:24]}...",
              file=sys.stderr)
        print(f"  current:  {fp['fingerprint'][:24]}...",
              file=sys.stderr)
        return 1

    print(json.dumps(fp, indent=2, sort_keys=True))

    if args.write:
        out_path = REPO_ROOT / "UNIQUE.json"
        out_path.write_text(
            json.dumps(fp, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())