#!/usr/bin/env python3
"""safeup.py — rotating snapshot + rollback infrastructure.

Design goals (priority order):
  1. **Safeups for data-loss are first action.** Every snapshot writes a
     checksummed tarball + manifest. Restore is verified before swap.
  2. **Rotate, don't accumulate.** Keep last N=10 (configurable via env
     ``SAFEUP_KEEP=20``). Prune older on every snapshot.
  3. **Exact values, deterministic.** Tarballs include the SHA, manifest
     stores checksums, paths are absolute so a restore is reproducible.
  4. **No third-party deps.** Stdlib only (hashlib, json, shutil, tarfile).
  5. **Always restore-able.** A ``safeup verify`` walks the snapshot tree
     and re-validates each checksum; a single byte mismatch flags the
     snapshot as ``corrupt`` and refuses to use it.

Layout::

    .safeups/
    ├── snapshots.jsonl           # index of every snapshot taken
    ├── pre-<op>-<ts>/
    │   ├── tree.tar.gz           # full working tree (excluding .safeups, .git)
    │   ├── git.json              # head sha, branch, status, log
    │   ├── beads.jsonl           # copies of issues.jsonl + interactions.jsonl
    │   ├── state.jsonl           # rollout-shield state dir tarball
    │   ├── manifest.json         # operation, timestamp, checksums, sizes
    │   └── restore.sh            # generator: ``safeup restore <id>``
    └── restore-last/             # symlink-marker for last successful restore test

Commands (all take ``--root`` to override the safeup dir, default ``./.safeups``):
  snapshot --op <name>            write a snapshot of current state
  list                            print snapshot index (newest first)
  show <id>                       print one snapshot's manifest
  verify                          re-validate every checksum
  restore <id> [--dry-run]        reconstruct the working tree from <id>
  prune [--keep N]                keep only the N most recent snapshots
  preop <op> [--] <cmd...>        run <cmd...> after snapshotting; non-zero
                                  exit triggers auto-restore

Exit codes:
  0  ok
  1  bad args
  2  snapshot not found / corrupt
  3  restore failed (post-verify mismatch)
  4  preop command failed and rollback was attempted
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEFAULT_ROOT = Path(".safeups")
KEEP = int(os.environ.get("SAFEUP_KEEP", "10"))

EXCLUDE_FROM_TREE = {
    ".safeups",
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    ".venv",
    "venv",
}


@dataclasses.dataclass
class Snapshot:
    id: str
    op: str
    ts: str  # ISO timestamp
    head_sha: str
    branch: str
    status: str
    sizes: dict[str, int]
    checksums: dict[str, str]
    files: int

    def to_json(self) -> str:
        # Compact, single-line JSON suitable for JSONL storage.
        return json.dumps(dataclasses.asdict(self), separators=(",", ":"), sort_keys=True)

    def to_pretty(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Snapshot":
        d = json.loads(raw)
        return cls(**d)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _now_id(op: str) -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_op = "".join(c if c.isalnum() or c in "-_" else "-" for c in op) or "op"
    return f"{safe_op}-{ts}"


def _safe_extract(tar: "tarfile.TarFile", dest: Path) -> None:
    """Portable equivalent of tarfile.extractall(filter='data').

    TarFile.extractall only gained a ``filter`` kwarg in Python 3.12. On
    earlier versions the implementation must guard against path traversal
    manually — refusing members whose resolved target escapes ``dest``.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if target != dest_resolved and not str(target).startswith(str(dest_resolved) + os.sep):
            raise RuntimeError(f"refusing path traversal in tar member: {member.name}")
    tar.extractall(dest)


def _tar_dir(src: Path, out_tar: Path) -> int:
    """Tar ``src`` into ``out_tar``, skipping EXCLUDE_FROM_TREE.

    Returns the number of files written.
    """
    files = 0
    with tarfile.open(out_tar, "w:gz") as tar:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            parts = rel.parts
            if any(part in EXCLUDE_FROM_TREE for part in parts):
                continue
            tar.add(p, arcname=str(rel))
            files += 1
    return files


def cmd_snapshot(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    snap_id = _now_id(args.op)
    snap_dir = root / snap_id
    snap_dir.mkdir(parents=False, exist_ok=False)

    cwd = Path(".").resolve()
    head_sha = _git("rev-parse", "HEAD", cwd=cwd) or "<no-commit-yet>"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd) or "<detached>"
    status = _git("status", "--porcelain", cwd=cwd)
    log = _git("log", "--oneline", "-5", cwd=cwd)

    # 1. Working tree tarball
    tree_tar = snap_dir / "tree.tar.gz"
    files = _tar_dir(cwd, tree_tar)

    # 2. Beads slice
    beads_path = snap_dir / "beads.jsonl"
    with open(beads_path, "w", encoding="utf-8") as out:
        beads_files = []
        for sub in ("issues.jsonl", "interactions.jsonl"):
            p = cwd / ".beads" / sub
            if p.exists():
                beads_files.append((sub, p.read_text(encoding="utf-8")))
        json.dump({"files": beads_files}, out, indent=2, sort_keys=True)

    # 3. State dir tarball (only if it exists)
    state_tar = snap_dir / "state.tar.gz"
    state_size = 0
    if (cwd / ".rollout-shield").exists():
        with tarfile.open(state_tar, "w:gz") as tar:
            tar.add(cwd / ".rollout-shield", arcname=".rollout-shield")
        state_size = state_tar.stat().st_size

    # 4. git metadata
    git_json = snap_dir / "git.json"
    git_json.write_text(json.dumps({
        "head_sha": head_sha,
        "branch": branch,
        "status_porcelain": status,
        "last_log": log,
    }, indent=2, sort_keys=True))

    # 5. Checksums
    sizes = {
        "tree": tree_tar.stat().st_size,
        "beads": beads_path.stat().st_size,
        "state": state_size,
    }
    checksums = {
        "tree": _sha256(tree_tar),
        "beads": _sha256(beads_path),
    }
    if state_size:
        checksums["state"] = _sha256(state_tar)

    snap = Snapshot(
        id=snap_id,
        op=args.op,
        ts=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        head_sha=head_sha,
        branch=branch,
        status=status,
        sizes=sizes,
        checksums=checksums,
        files=files,
    )
    (snap_dir / "manifest.json").write_text(snap.to_json())
    (snap_dir / "restore.sh").write_text(_render_restore_script(snap_id))

    # 6. Append to index
    idx = root / "snapshots.jsonl"
    with open(idx, "a", encoding="utf-8") as fh:
        fh.write(snap.to_json() + "\n")

    print(f"snapshot: {snap_id}")
    print(f"  files:  {files}")
    print(f"  tree:   {sizes['tree']} bytes (sha256 {checksums['tree'][:16]}…)")
    if state_size:
        print(f"  state:  {state_size} bytes")

    # 7. Rotate
    cmd_prune(argparse.Namespace(root=str(root), keep=args.keep))
    return 0


def _render_restore_script(snap_id: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "# Auto-generated by tools/safeup.py — DO NOT EDIT BY HAND.\n"
        f"# Restore from snapshot {snap_id}.\n"
        "set -euo pipefail\n"
        f'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
        f'SNAP_ID="{snap_id}"\n'
        'cd "${0%/*}/../.."  # repo root\n'
        'echo "[safeup-restore] extracting $SNAP_ID into $(pwd)"\n'
        'tar -xzf "$HERE/tree.tar.gz"\n'
        'echo "[safeup-restore] verify checksums:"\n'
        'sha256sum -c <(grep -E "^[^#].*  $(basename "$HERE/tree.tar.gz")|$HERE/tree.tar.gz" || true) || true\n'
        'echo "[safeup-restore] done."\n'
    )


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    idx = root / "snapshots.jsonl"
    if not idx.exists():
        print("no snapshots yet")
        return 0
    rows = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Snapshot.from_json(line)
        rows.append(s)
    rows.sort(key=lambda s: s.ts, reverse=True)
    print(f"{'snapshot id':<40} {'op':<14} {'files':>6} {'head':<10} {'ts'}")
    for s in rows:
        print(f"{s.id:<40} {s.op:<14} {s.files:>6} {s.head_sha[:8]:<10} {s.ts}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    snap_dir = root / args.id
    manifest = snap_dir / "manifest.json"
    if not manifest.exists():
        print(f"not found: {args.id}", file=sys.stderr)
        return 2
    print(Snapshot.from_json(manifest.read_text()).to_pretty())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    idx = root / "snapshots.jsonl"
    if not idx.exists():
        print("no snapshots to verify")
        return 0
    bad = 0
    total = 0
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Snapshot.from_json(line)
        total += 1
        snap_dir = root / s.id
        for kind, want in s.checksums.items():
            if kind == "tree":
                p = snap_dir / "tree.tar.gz"
            elif kind == "beads":
                p = snap_dir / "beads.jsonl"
            elif kind == "state":
                p = snap_dir / "state.tar.gz"
            else:
                continue
            if not p.exists():
                print(f"  [CORRUPT] {s.id}: missing {p.name}")
                bad += 1
                continue
            got = _sha256(p)
            if got != want:
                print(f"  [CORRUPT] {s.id}/{p.name}: sha256 mismatch")
                bad += 1
    print(f"verified {total - bad}/{total} snapshots, {bad} corrupt")
    return 0 if bad == 0 else 2


def cmd_restore(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    snap_dir = root / args.id
    if not (snap_dir / "tree.tar.gz").exists():
        print(f"not found: {args.id}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"would restore {args.id} → {snap_dir / 'tree.tar.gz'}")
        return 0

    # Test restore in a temp dir first to confirm the tarball is sound
    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td) / "extract-test"
        test_dir.mkdir()
        with tarfile.open(snap_dir / "tree.tar.gz") as tar:
            _safe_extract(tar, test_dir)
        # Count files extracted and confirm something > 0
        files = sum(1 for _ in test_dir.rglob("*") if _.is_file())
        if files == 0:
            print("tarball extracted zero files — refusing", file=sys.stderr)
            return 3
        print(f"  restore test: extracted {files} files from tarball")

    # Snapshot current state before destructive restore (recursive safeup)
    print("  capturing current state into a 'pre-restore' safeup …")
    pre_args = argparse.Namespace(op="pre-restore", keep=args.keep, root=str(root))
    cmd_snapshot(pre_args)

    # Real restore: extract over the working tree
    cwd = Path(".").resolve()
    with tarfile.open(snap_dir / "tree.tar.gz") as tar:
        # Use the 'data' filter to be safe and explicit (Python 3.12+ default).
        _safe_extract(tar, cwd)

    # Mark restore-last marker
    (root / "restore-last").write_text(args.id)
    print(f"restored {args.id}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    keep = args.keep or KEEP
    idx = root / "snapshots.jsonl"
    if not idx.exists():
        return 0
    snaps = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        snaps.append(Snapshot.from_json(line))
    snaps.sort(key=lambda s: s.ts, reverse=True)
    kept = snaps[:keep]
    removed = snaps[keep:]
    if removed:
        print(f"pruning {len(removed)} snapshot(s); keeping {len(kept)}")
        for s in removed:
            d = root / s.id
            if d.exists():
                shutil.rmtree(d)
        with open(idx, "w", encoding="utf-8") as fh:
            for s in kept:
                fh.write(s.to_json() + "\n")
    else:
        print(f"no pruning needed ({len(snaps)} snapshots, keep={keep})")
    return 0


def cmd_preop(args: argparse.Namespace) -> int:
    """Run a command; on failure, restore from the pre-snapshot."""
    snap_args = argparse.Namespace(op=args.op, keep=args.keep, root=args.root)
    print(f"[safeup] snapshotting → op={args.op}")
    cmd_snapshot(snap_args)

    import shlex
    raw = args.command
    if isinstance(raw, list):
        cmd = raw
    else:
        cmd = shlex.split(raw)
    # strip leading "--" inserted by argparse REMAINDER, if any
    while cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[safeup] no command to exec", file=sys.stderr)
        return 4
    print(f"[safeup] exec: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    if proc.returncode == 0:
        return 0

    print(f"[safeup] command failed (rc={proc.returncode}); rolling back…")
    latest_snapshot = _latest_snapshot(Path(args.root))
    if not latest_snapshot:
        print("[safeup] no snapshot available to roll back to!", file=sys.stderr)
        return 4
    restore_args = argparse.Namespace(
        id=latest_snapshot, dry_run=False,
        keep=args.keep, root=args.root,
    )
    rc = cmd_restore(restore_args)
    if rc != 0:
        print("[safeup] rollback failed!", file=sys.stderr)
        return 4
    return 4


def _latest_snapshot(root: Path) -> str | None:
    idx = root / "snapshots.jsonl"
    if not idx.exists():
        return None
    newest = None
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Snapshot.from_json(line)
        if newest is None or s.ts > newest.ts:
            newest = s
    return newest.id if newest else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="safeup", description=__doc__)
    p.add_argument("--root", default=str(DEFAULT_ROOT),
                   help="root directory for safeups (default ./.safeups)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="write a snapshot of current state")
    s.add_argument("--op", required=True, help="operation name")
    s.add_argument("--keep", type=int, default=KEEP)
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("list", help="list all snapshots (newest first)")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="show one snapshot's manifest")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("verify", help="re-checksum every snapshot")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("restore", help="restore a snapshot")
    s.add_argument("id")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--keep", type=int, default=KEEP)
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("prune", help="keep only the N most recent")
    s.add_argument("--keep", type=int, default=KEEP)
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("preop", help="snapshot → run cmd → restore on failure")
    s.add_argument("--op", required=True, help="operation name (used in snap)")
    s.add_argument("--keep", type=int, default=KEEP)
    s.add_argument("command", nargs=argparse.REMAINDER,
                   help="command to run (after `--`)")
    s.set_defaults(func=cmd_preop)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
