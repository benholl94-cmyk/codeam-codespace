"""Write-lock + atomic-durability tests for the state layer."""
import errno
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from rollout_shield.state import (
    State,
    StateLockError,
    atomic_write_json,
    lock_path,
    write_lock,
)


class TestWriteLock(unittest.TestCase):
    def test_write_lock_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            with write_lock(Path(d)):
                # while holding the lock, the lock file exists
                self.assertTrue(lock_path(Path(d)).exists())

    def test_write_lock_releases_on_exit(self):
        with tempfile.TemporaryDirectory() as d:
            lockfile = lock_path(Path(d))
            with write_lock(Path(d)):
                pass
            # after exit, another writer can acquire
            with write_lock(Path(d), blocking=False):
                self.assertTrue(lockfile.exists())

    def test_write_lock_nonblocking_when_held(self):
        with tempfile.TemporaryDirectory() as d:
            inner_done = threading.Event()
            outer_done = threading.Event()
            outer_got_lock = threading.Event()

            def hold_lock():
                with write_lock(Path(d), blocking=False):
                    inner_done.set()
                    outer_done.wait(timeout=5)

            t = threading.Thread(target=hold_lock, daemon=True)
            t.start()
            inner_done.wait(timeout=5)
            # main thread attempts to grab the lock non-blocking -> StateLockError
            with self.assertRaises(StateLockError):
                with write_lock(Path(d), blocking=False):
                    outer_got_lock.set()
            outer_done.set()
            t.join(timeout=5)

    def test_atomic_write_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            atomic_write_json(p, {"ok": True, "ts": 1234})
            self.assertTrue(p.exists())
            # second write replaces the first; no leftover .tmp
            atomic_write_json(p, {"ok": False, "ts": 9999})
            self.assertTrue(p.exists())
            leftovers = list(Path(d).iterdir())
            self.assertEqual(len(leftovers), 1, f"unexpected files: {leftovers}")


if __name__ == "__main__":
    unittest.main()
