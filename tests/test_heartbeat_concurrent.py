"""Tests for the G1 cross-process atomicity fix in tools/audit_log.heartbeat.

Verifies that concurrent calls produce exactly one entry; sequential
calls (including sequential --force) are unaffected.

Note on isolation: ``audit_log.AUDIT_DIR`` is computed at import time
from the ``ROLLOUT_SHIELD_AUDIT`` env var. To isolate per-test, each
test chdir's into a fresh tmpdir so the relative ``.audit`` resolves
to the right place.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import audit_log as _al  # noqa: E402


class TestConcurrentHeartbeat(unittest.TestCase):
    """Race: N threads each call heartbeat(force=True). Exactly one wins."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self.tmp = Path(tempfile.mkdtemp())
        os.chdir(self.tmp)
        # Mirror append(): ensure AUDIT_DIR exists so O_CREAT has a parent.
        os.makedirs(".audit", exist_ok=True)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _force_audit_dir(self):
        """Update AUDIT_DIR / HB_LOCK_FILE references to the chdir-resolved
        location. They were computed at import time but the relative
        '.audit' path resolves against cwd, so write/read still target
        the right place as long as we never resolved absolute paths.
        """
        # AUDIT_DIR and HB_LOCK_FILE are Path('.audit') / Path('.audit/.hb.lock').
        # With chdir(self.tmp), .audit resolves to self.tmp/.audit. ✓ — nothing
        # to do here; the chdir is the fix.
        pass

    def test_two_threads_racing_only_one_wins(self):
        self._force_audit_dir()
        barrier = threading.Barrier(2)
        results: list = []

        def worker():
            barrier.wait()
            results.append(_al.heartbeat(force=True))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        wins = [r for r in results if r is not None]
        losses = [r for r in results if r is None]
        self.assertEqual(len(wins), 1,
                         f"expected 1 winner, got {len(wins)} (results={results})")
        self.assertEqual(len(losses), 1,
                         f"expected 1 loser, got {len(losses)} (results={results})")
        log = Path(".audit/audit.jsonl")
        self.assertTrue(log.exists(), "winner thread should have appended an entry")
        entries = log.read_text().splitlines()
        self.assertEqual(
            sum(1 for e in entries if '"action": "heartbeat"' in e), 1,
            "exactly one heartbeat entry expected in the log",
        )

    def test_sequential_force_still_appends_each_time(self):
        self._force_audit_dir()
        results = []
        for _ in range(3):
            results.append(_al.heartbeat(force=True))
        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 3)

    def test_lockfile_cleaned_up_after_call(self):
        self._force_audit_dir()
        _al.heartbeat(force=True)
        self.assertFalse(Path(".audit/.hb.lock").exists(),
                         "lockfile left behind after successful heartbeat")

    def test_stale_lockfile_is_pruned(self):
        self._force_audit_dir()
        lock = Path(".audit/.hb.lock")
        lock.touch()
        os.utime(lock, (time.time() - 60, time.time() - 60))
        result = _al.heartbeat(force=False)
        self.assertIsNotNone(result, "should have pruned stale lock and appended")
        self.assertFalse(lock.exists())

    def test_no_heartbeat_no_lock_leak_on_idempotent_return(self):
        self._force_audit_dir()
        _al.heartbeat(force=True)
        second = _al.heartbeat(force=False)
        self.assertIsNone(second, "second heartbeat within 24h must be None")
        self.assertFalse(Path(".audit/.hb.lock").exists())


if __name__ == "__main__":
    unittest.main()
