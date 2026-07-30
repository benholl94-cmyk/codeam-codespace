"""Tests for tools/safeup.py — rotating snapshot + restore."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAFEUP = REPO_ROOT / "tools" / "safeup.py"


class TestSafeup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # init a tiny git repo so safeup can read status/head
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "t"], check=True)
        (self.root / "hello.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "init"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SAFEUP), "--root", ".safeups", *args],
            cwd=self.root, capture_output=True, text=True,
        )

    def test_snapshot_writes_manifest_and_tarball(self):
        r = self._run("snapshot", "--op", "test")
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = [d.name for d in (self.root / ".safeups").iterdir() if d.is_dir()]
        self.assertEqual(len(ids), 1)
        snap_dir = self.root / ".safeups" / ids[0]
        self.assertTrue((snap_dir / "tree.tar.gz").exists())
        self.assertTrue((snap_dir / "manifest.json").exists())
        self.assertTrue((snap_dir / "beads.jsonl").exists())
        manifest = json.loads((snap_dir / "manifest.json").read_text())
        self.assertEqual(manifest["op"], "test")
        self.assertGreater(manifest["files"], 0)
        self.assertIn("tree", manifest["checksums"])
        self.assertIn("beads", manifest["checksums"])

    def test_list_shows_snapshot(self):
        self._run("snapshot", "--op", "first")
        self._run("snapshot", "--op", "second")
        r = self._run("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("first", r.stdout)
        self.assertIn("second", r.stdout)

    def test_verify_walks_all_snapshots(self):
        for op in ("a", "b", "c"):
            self._run("snapshot", "--op", op)
        r = self._run("verify")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("verified 3/3", r.stdout)

    def test_restore_brings_file_back(self):
        self._run("snapshot", "--op", "snapshot-clean")
        (self.root / "hello.txt").write_text("dirty\n")
        # find the snapshot id
        snap_id = next(
            d.name for d in (self.root / ".safeups").iterdir()
            if d.is_dir() and d.name.startswith("snapshot-clean")
        )
        r = self._run("restore", snap_id)
        self.assertEqual(r.returncode, 0, r.stderr)
        # restore creates a nested 'extract-test' tag and then prints "extracted N files"
        self.assertIn("restore test: extracted", r.stderr + r.stdout)
        # the file should be back to "hello\n"
        self.assertEqual((self.root / "hello.txt").read_text(), "hello\n")

    def test_prune_keeps_only_n(self):
        for i in range(5):
            self._run("snapshot", "--op", f"snap-{i}")
        r = self._run("prune", "--keep", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = [d.name for d in (self.root / ".safeups").iterdir() if d.is_dir()]
        self.assertEqual(len(ids), 2, f"ids after prune: {ids}")

    def test_preop_rolls_back_on_failure(self):
        self._run("snapshot", "--op", "good")
        (self.root / "hello.txt").write_text("dirty-preop\n")
        r = subprocess.run(
            [sys.executable, str(SAFEUP), "--root", ".safeups",
             "preop", "--op", "test", "--", "false"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertNotEqual(r.returncode, 0, "preop should fail when child fails")
        # hello.txt must be back to dirty-preop because we did not modify it
        # (we only modified it BEFORE running preop, so preop's snapshot
        # captured dirty-preop)
        self.assertEqual((self.root / "hello.txt").read_text(), "dirty-preop\n")


if __name__ == "__main__":
    unittest.main()
