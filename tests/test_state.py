"""State layer — round-trip persistence, atomic write, lockfile, error handling."""
import json
import tempfile
import unittest
from pathlib import Path

from rollout_shield.state import State, atomic_write_json, SCHEMA_VERSION


class TestAtomicWriteJson(unittest.TestCase):
    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            payload = {"schema_version": 1, "agents": {"alice": {"score": 1.0}}}
            atomic_write_json(p, payload)
            self.assertTrue(p.exists())
            data = json.loads(p.read_text())
            self.assertEqual(data, payload)

    def test_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            atomic_write_json(p, {"v": 1})
            atomic_write_json(p, {"v": 2, "schema_version": SCHEMA_VERSION})
            self.assertEqual(json.loads(p.read_text()), {"v": 2, "schema_version": SCHEMA_VERSION})

    def test_no_leftover_tmp_files(self):
        """Atomic write must clean up its staging file even if the rename succeeds."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            atomic_write_json(p, {"v": 1})
            leftovers = [q for q in Path(d).iterdir() if q.name.startswith(".")]
            self.assertEqual(leftovers, [], f"leftover tmp files: {leftovers}")


class TestStateRoundTrip(unittest.TestCase):
    def test_save_load_config_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            state = State(root=Path(d))
            cfg = {"agents": {"alice": {"score": 1.5, "history": []}}, "schema_version": SCHEMA_VERSION}
            state.save_config(cfg)
            loaded = state.load_config()
            self.assertEqual(loaded["agents"]["alice"]["score"], 1.5)

    def test_save_load_reputation_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            state = State(root=Path(d))
            state.update_reputation("alice", 0.1, "first-sign")
            state.update_reputation("alice", -0.05, "soft-dispute")
            rep = state.load_reputation()
            self.assertIn("alice", rep["agents"])
            entry = rep["agents"]["alice"]
            self.assertAlmostEqual(entry["score"], 0.05, places=4)
            self.assertEqual(len(entry["history"]), 2)
            self.assertEqual(entry["history"][-1]["reason"], "soft-dispute")

    def test_default_state_has_schema_version(self):
        with tempfile.TemporaryDirectory() as d:
            state = State(root=Path(d))
            cfg = state.load_config()
            self.assertIn("schema_version", cfg)
            self.assertEqual(cfg["schema_version"], SCHEMA_VERSION)

    def test_creates_required_subdirs(self):
        with tempfile.TemporaryDirectory() as d:
            state = State(root=Path(d))
            for sub in ("claims", "alerts", "health", "keys"):
                self.assertTrue((Path(d) / sub).is_dir(), f"missing: {sub}")


if __name__ == "__main__":
    unittest.main()
