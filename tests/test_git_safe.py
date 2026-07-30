"""Test git-safe wrapper's snapshot + pass-through behavior."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_SAFE = REPO_ROOT / "tools" / "git-safe"


class TestGitSafe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "t"], check=True)
        # copy tools/ into the test repo so git-safe is reachable
        dst = self.root / "tools"
        dst.mkdir()
        for f in ("safeup.py", "git-safe"):
            subprocess.run(["cp", str(REPO_ROOT / "tools" / f), str(dst / f)], check=True)
        (dst / "safeup.py").chmod(0o755)
        (dst / "git-safe").chmod(0o755)
        (self.root / "f.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "init"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_non_commit_push_passes_through(self):
        r = subprocess.run(
            [str(GIT_SAFE), "status"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("On branch", r.stdout)

    def test_commit_snapshots_and_lands(self):
        (self.root / "f.txt").write_text("b\n")
        r = subprocess.run(
            [str(GIT_SAFE), "commit", "-am", "second"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue((self.root / ".safeups").exists())
        # safeup created a snapshot with op=git-commit-...
        snaps = [d for d in (self.root / ".safeups").iterdir() if d.is_dir()]
        self.assertEqual(len(snaps), 1)
        self.assertTrue(snaps[0].name.startswith("git-commit-"))


if __name__ == "__main__":
    unittest.main()
