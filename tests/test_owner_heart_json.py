"""JSON-contract + adversarial probes for tools/owner_heart.py.

Two layers:
  SchemaStability  — the JSON keys are a public contract for CI consumers;
                    breaking them is a breaking change.
  Adversarial      — try to break the module's invariants; if any of these
                    asserts, owner_heart.py has a real bug to fix.
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
AUDIT_LOG = REPO_ROOT / "tools" / "audit_log.py"
PYTHON = sys.executable

_REQUIRED_HEART = ("status", "action", "forced", "entry")
_REQUIRED_CHECK_INITIAL = ("status", "exit_code", "age_seconds",
                            "warn_threshold_hours", "fail_threshold_hours")
_REQUIRED_CHECK_FRESH = _REQUIRED_CHECK_INITIAL + ("age_hours",)
_REQUIRED_TAIL = ("count", "entries", "n_requested")


def _run(tmp, *args):
    env = os.environ.copy()
    env["ROLLOUT_SHIELD_AUDIT"] = str(tmp / ".audit")
    return subprocess.run(
        [PYTHON, str(OWNER_HEART), *args],
        cwd=str(tmp), capture_output=True, text=True, env=env,
    )


def _run_audit_log(*args):
    """Run tools/audit_log.py in-process (uses the real ~/.audit)."""
    return subprocess.run(
        [PYTHON, str(AUDIT_LOG), *args],
        capture_output=True, text=True,
    )


class TestSchemaStabilityHeart(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heart_payload_keys_are_stable(self):
        r = _run(self.tmp, "--json", "heart")
        payload = json.loads(r.stdout.strip())
        for k in _REQUIRED_HEART:
            self.assertIn(k, payload, f"missing required key: {k}")

    def test_heart_already_beat_payload_keys(self):
        _run(self.tmp, "heart")
        r = _run(self.tmp, "--json", "heart")
        payload = json.loads(r.stdout.strip())
        self.assertEqual(payload["status"], "already_beat")
        self.assertIn("within_seconds", payload)
        self.assertIn("force_bypass_available", payload)


class TestSchemaStabilityCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_initial_payload_keys(self):
        r = _run(self.tmp, "--json", "check")
        payload = json.loads(r.stdout.strip())
        for k in _REQUIRED_CHECK_INITIAL:
            self.assertIn(k, payload, f"missing required key: {k}")

    def test_check_fresh_payload_keys(self):
        _run(self.tmp, "heart")
        r = _run(self.tmp, "--json", "check")
        payload = json.loads(r.stdout.strip())
        for k in _REQUIRED_CHECK_FRESH:
            self.assertIn(k, payload, f"missing required key: {k}")


class TestSchemaStabilityTail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tail_payload_keys(self):
        r = _run(self.tmp, "--json", "tail")
        payload = json.loads(r.stdout.strip())
        for k in _REQUIRED_TAIL:
            self.assertIn(k, payload, f"missing required key: {k}")


class TestAdversarialChainIntegrity(unittest.TestCase):
    """--force must NOT break the hash chain."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_many_forces_keep_chain_valid(self):
        for _ in range(50):
            r = _run(self.tmp, "--json", "heart", "--force")
            self.assertEqual(r.returncode, 0, r.stderr)

        log = self.tmp / ".audit" / "audit.jsonl"
        self.assertTrue(log.exists())
        entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        self.assertEqual(len(entries), 50)

        # Walk prev-hash chain.
        prev = "0" * 64
        for i, e in enumerate(entries):
            self.assertEqual(e["prev"], prev,
                             f"entry {i} prev mismatch")
            prev = e["hash"]


class TestAdversarialFirstEntry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_heart_uses_genesis_prev(self):
        r = _run(self.tmp, "--json", "heart")
        payload = json.loads(r.stdout.strip())
        self.assertEqual(payload["entry"]["prev"], "0" * 64)


class TestAdversarialCorruptPriorLine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heart_appends_cleanly_after_corrupt_jsonl(self):
        """If the prior line is corrupt JSON, append should still write a
        valid new entry (caller-side robustness; audit_log ignores bad
        lines on read)."""
        audit = self.tmp / ".audit"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / "audit.jsonl").write_text("not valid json\n")
        r = _run(self.tmp, "--json", "heart")
        # Heart accepts (writes its own genesis-chained entry); audit_log
        # append() doesn't read.
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (audit / "audit.jsonl").read_text()
        self.assertIn('"action": "heartbeat"', log)


class TestAdversarialThresholdBrackets(unittest.TestCase):
    """24h (WARN) and 26h (FAIL) thresholds must be strict (>, not >=)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_just_under_24h_is_ok(self):
        _run(self.tmp, "heart")
        # Touch HEARTBEAT_FILE mtime to 23h ago.
        hb = self.tmp / ".audit" / "last_heartbeat"
        if hb.exists():
            import time
            st = hb.stat()
            os.utime(hb, (st.st_atime, time.time() - 23 * 3600))
        r = _run(self.tmp, "--json", "check")
        self.assertEqual(json.loads(r.stdout.strip())["status"], "ok")

    def test_25h_is_stale(self):
        _run(self.tmp, "heart")
        hb = self.tmp / ".audit" / "last_heartbeat"
        if hb.exists():
            import time
            st = hb.stat()
            os.utime(hb, (st.st_atime, time.time() - 25 * 3600))
        r = _run(self.tmp, "--json", "check")
        payload = json.loads(r.stdout.strip())
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["exit_code"], 1)

    def test_27h_is_broken(self):
        _run(self.tmp, "heart")
        hb = self.tmp / ".audit" / "last_heartbeat"
        if hb.exists():
            import time
            st = hb.stat()
            os.utime(hb, (st.st_atime, time.time() - 27 * 3600))
        r = _run(self.tmp, "--json", "check")
        payload = json.loads(r.stdout.strip())
        self.assertEqual(payload["status"], "broken")
        self.assertEqual(payload["exit_code"], 2)


class TestAdversarialDoubleCheckIdempotence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_is_idempotent_read_only(self):
        """check must not write to the audit log."""
        _run(self.tmp, "heart")
        # Snapshot the log.
        log = self.tmp / ".audit" / "audit.jsonl"
        before = log.read_text() if log.exists() else ""
        for _ in range(5):
            r = _run(self.tmp, "--json", "check")
            self.assertEqual(r.returncode, 0)
        after = log.read_text() if log.exists() else ""
        self.assertEqual(before, after, "check must not modify the log")


if __name__ == "__main__":
    unittest.main()
