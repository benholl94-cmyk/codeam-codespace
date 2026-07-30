"""Tests for tools/owner_heart.py — heartbeat write + age-check.

Asserts against --json mode (the machine-readable contract). Mirrors
tests/test_safeup.py pattern for subprocess isolation.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNER_HEART = REPO_ROOT / "tools" / "owner_heart.py"
PYTHON = sys.executable


def _run(tmp, *args):
    env = os.environ.copy()
    env["ROLLOUT_SHIELD_AUDIT"] = str(tmp / ".audit")
    return subprocess.run(
        [PYTHON, str(OWNER_HEART), *args],
        cwd=str(tmp), capture_output=True, text=True, env=env,
    )


def _payload(r):
    """Parse stdout as JSON. Fails the test clearly if invalid JSON."""
    return json.loads(r.stdout.strip())


class TestOwnerHeartWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heart_writes_entry_and_marker(self):
        r = _run(self.tmp, "--json", "heart")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = _payload(r)
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["entry"]["action"], "heartbeat")
        self.assertEqual(p["entry"]["actor"], "system")
        r2 = _run(self.tmp, "--json", "check")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(_payload(r2)["status"], "ok")


class TestOwnerHeartIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_heart_within_24h_refuses(self):
        self.assertEqual(_run(self.tmp, "--json", "heart").returncode, 0)
        r2 = _run(self.tmp, "--json", "heart")
        self.assertEqual(r2.returncode, 1, r2.stderr)
        p = _payload(r2)
        self.assertEqual(p["status"], "already_beat")
        self.assertTrue(p["force_bypass_available"])

    def test_force_bypasses_24h_gate(self):
        self.assertEqual(_run(self.tmp, "--json", "heart").returncode, 0)
        r2 = _run(self.tmp, "--json", "heart", "--force")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        log = (self.tmp / ".audit" / "audit.jsonl").read_text()
        self.assertEqual(log.count('"action": "heartbeat"'), 2)


class TestOwnerHeartCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_initial_state_when_no_log(self):
        r = _run(self.tmp, "--json", "check")
        self.assertEqual(r.returncode, 0)
        p = _payload(r)
        self.assertEqual(p["status"], "initial")
        self.assertIsNone(p["age_seconds"])

    def test_check_broken_when_log_has_entries_but_no_heartbeat(self):
        audit = self.tmp / ".audit"
        audit.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": "2026-07-29T00:00:00Z", "actor": "test",
            "action": "synthetic", "target": "", "ok": True,
            "detail": {}, "prev": "0" * 64, "hash": "0" * 64,
        }
        (audit / "audit.jsonl").write_text(
            json.dumps(entry, sort_keys=True) + "\n"
        )
        r = _run(self.tmp, "--json", "check")
        self.assertEqual(r.returncode, 2)
        p = _payload(r)
        self.assertEqual(p["status"], "broken")
        self.assertEqual(p["reason"], "no_heartbeat_but_log_has_entries")

    def test_check_ok_after_fresh_heart(self):
        _run(self.tmp, "--json", "heart")
        r = _run(self.tmp, "--json", "check")
        self.assertEqual(r.returncode, 0)
        p = _payload(r)
        self.assertEqual(p["status"], "ok")
        self.assertLess(p["age_seconds"], 60)


class TestOwnerHeartTail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tail_lists_recent_heartbeats(self):
        _run(self.tmp, "--json", "heart", "--force")
        _run(self.tmp, "--json", "heart", "--force")
        r = _run(self.tmp, "--json", "tail")
        self.assertEqual(r.returncode, 0)
        p = _payload(r)
        self.assertEqual(p["count"], 2)
        self.assertEqual(len(p["entries"]), 2)
        for e in p["entries"]:
            self.assertEqual(e["action"], "heartbeat")


class TestOwnerHeartHelp(unittest.TestCase):
    def test_help_lists_all_subcommands(self):
        r = subprocess.run(
            [PYTHON, str(OWNER_HEART), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("heart", "check", "tail", "--json"):
            self.assertIn(cmd, r.stdout)


if __name__ == "__main__":
    unittest.main()
