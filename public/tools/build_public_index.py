#!/usr/bin/env python3
"""Build the public one-way index from the source tree.

The public/ directory is the **only** surface outsiders may read or
copy. It contains a curated subset of files from the source repo:

  * README.md, LICENSE, BRAND.md            — front matter
  * docs/*.md                                — public docs
  * rollout_shield/ (filtered)               — the runtime CLI
  * tests/test_identity.py                   — proves the identity
    feature is real and verifiable
  * tools/build_uniqueness.py + UNIQUE.json  — proves uniqueness
  * public/INDEX.json                        — generated manifest

Everything else (private keys, agent identities, internal plans,
beads, audit logs, state) is **excluded** by name. The script
refuses to copy anything from `EXCLUDE_DIRS` or matching
`EXCLUDE_GLOBS`.

The index is one-way: external consumers can read public/ but
have no path to anything in INTERNAL/. There is no symlink, no
relative ../, no exported bundle. The only legal way to reach
INTERNAL/ is via the user+model's identity token, which the
runtime checks at every call site (see rollout_shield/unique.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = REPO_ROOT / "public"

# Hard-excluded: directories that contain internals and must NEVER
# appear in public/. Anything under these is operator-side state.
EXCLUDE_DIRS = frozenset({
    ".beads",
    ".git",
    "state",          # runtime state roots
    "keys_material",  # private keys (defense in depth)
    "access",         # audit logs
    "identity",       # pseudonym chain + conflicts
    "reputation",
    "claims",
    "alerts",
    "monitors",
    "snapshots",
    "safeups",
    "tools/secure_state.py",   # paper-phrase recovery — operator-only
    "tools/safeup.py",         # snapshot helper — operator-only
})

# Hard-excluded: file globs (path suffix match) that must never leak.
EXCLUDE_GLOBS = (
    "*.pem",
    "*.key",
    "*.seed",
    "*paper*phrase*",
    "*recovery*",
    "*backup*",
    "*secret*",
    "*password*",
    "*credentials*",
    "*private*",
    "*.beads",
)

# Files that may appear (whitelist), in addition to the always-
# included paths above. Used as a positive list to prevent drift.
# Note: INTERNAL.md is deliberately NOT in this list; it documents
# internals and is only safe to read on the operator side.
PUBLIC_INCLUDE = (
    "README.md",
    "LICENSE",
    "BRAND.md",
    "UNIQUE.md",
    "UNIQUE.json",
    "docs/",
    "rollout_shield/__init__.py",
    "rollout_shield/cli.py",
    "rollout_shield/state.py",
    "rollout_shield/identity.py",
    "rollout_shield/unique.py",
    "rollout_shield/audit.py",
    "rollout_shield/audit_log.py",
    "rollout_shield/policy_file.py",
    "rollout_shield/alerter.py",
    "rollout_shield/health_checks.py",
    "rollout_shield/monitor_daemon.py",
    "rollout_shield/http_server.py",
    "tests/test_identity.py",
    "tests/test_unique.py",
    "tools/build_uniqueness.py",
    "tools/build_public_index.py",
)


def _is_excluded(rel: Path) -> bool:
    """True if the file is in EXCLUDE_DIRS or matches a forbidden glob."""
    parts = rel.parts
    for d in EXCLUDE_DIRS:
        if d in parts:
            return True
    name = rel.name
    for glob in EXCLUDE_GLOBS:
        if glob.startswith("*") and name.endswith(glob[1:]):
            return True
    return False


def _included(rel: Path) -> bool:
    """True if the path matches the whitelist."""
    rel_str = rel.as_posix()
    for allowed in PUBLIC_INCLUDE:
        if allowed.endswith("/"):
            if rel_str.startswith(allowed) or rel.as_posix().startswith(
                allowed.rstrip("/")
            ):
                return True
        else:
            if rel_str == allowed:
                return True
    return False


def build_index(out_dir: Path) -> dict:
    """Copy allowed files into out_dir; write INDEX.json; return manifest."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "policy": "one-way public index; externals see this only",
        "included": [],
        "excluded_count": 0,
        "manifest_hash": "",
    }
    for src in sorted(REPO_ROOT.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(REPO_ROOT)
        if rel == Path("public"):
            continue
        if _is_excluded(rel):
            manifest["excluded_count"] += 1
            continue
        if not _included(rel):
            manifest["excluded_count"] += 1
            continue
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # re-chmod to 0644 so external consumers can read but never write
        dst.chmod(0o644)
        # record a per-file hash so consumers can verify integrity
        h = hashlib.sha256(dst.read_bytes()).hexdigest()
        manifest["included"].append({
            "path": rel.as_posix(),
            "sha256": h,
            "size": dst.stat().st_size,
        })

    # Manifest hash binds the manifest to its contents.
    body = json.dumps(manifest["included"], sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    manifest["manifest_hash"] = hashlib.sha256(body).hexdigest()
    (out_dir / "INDEX.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(PUBLIC),
                   help=f"output directory (default: {PUBLIC})")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-file output")
    args = p.parse_args(argv)
    manifest = build_index(Path(args.out))
    if not args.quiet:
        print(f"wrote {len(manifest['included'])} files to {args.out}")
        print(f"excluded {manifest['excluded_count']} files (internal)")
        print(f"manifest_hash: {manifest['manifest_hash'][:24]}...")
    # Also write a human-readable README so consumers know what
    # they're looking at.
    readme = Path(args.out) / "PUBLIC_README.md"
    readme.write_text(
        "# Public Index\n\n"
        "This directory is the **one-way public surface** of the "
        "rollout-shield repository. External consumers may read, copy, "
        "and audit the files here.\n\n"
        "The full repository contains internal-only state (private keys, "
        "identity chains, audit logs, plans, recovery phrases) that is "
        "**not** exposed in this index. Reaching the internals requires "
        "the operator's identity token; see `../INTERNAL.md` (if you "
        "have access) or `UNIQUE.md` for the access model.\n\n"
        f"* Manifest hash: `{manifest['manifest_hash']}`\n"
        f"* Files included: {len(manifest['included'])}\n"
        f"* Files excluded: {manifest['excluded_count']}\n\n"
        "See `INDEX.json` for the per-file manifest with SHA-256 hashes.\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())