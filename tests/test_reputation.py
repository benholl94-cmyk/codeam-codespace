"""End-to-end: claim create + verify increments the reputation index."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _rep_agents(rep_path: Path) -> dict:
    if not rep_path.exists():
        return {}
    return json.loads(rep_path.read_text()).get("agents", {})


class TestReputationWiring(unittest.TestCase):
    def test_reputation_increments_after_create_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            env_root = str(Path(d))
            rep_path = Path(d) / "reputation.json"

            # pre-condition: nothing has happened yet
            self.assertEqual(_rep_agents(rep_path), {})

            # create key
            subprocess.run(
                [sys.executable, "-m", "rollout_shield", "keys", "new",
                 "--agent-id", "rep-agent", "--state-root", env_root],
                check=True, capture_output=True, text=True, timeout=30,
            )

            # create one claim → +0.02 to "rep-agent"
            r1 = subprocess.run(
                [sys.executable, "-m", "rollout_shield", "claim", "create",
                 "--agent-id", "rep-agent", "--type", "change",
                 "--body", "reputation wiring test",
                 "--state-root", env_root],
                check=True, capture_output=True, text=True, timeout=30,
            )
            cid = None
            for line in r1.stdout.splitlines():
                if line.startswith("claim created:"):
                    cid = line.split()[2]
                    break
            self.assertIsNotNone(cid)

            # verify → +0.01 more
            subprocess.run(
                [sys.executable, "-m", "rollout_shield", "verify",
                 cid, "--state-root", env_root],
                check=True, capture_output=True, text=True, timeout=30,
            )

            # rep-agent should now have score ~ +0.03 and 2 history entries
            entry = _rep_agents(rep_path)["rep-agent"]
            self.assertGreaterEqual(entry["score"], 0.02)
            self.assertLessEqual(entry["score"], 0.04)
            reasons = {h["reason"] for h in entry["history"]}
            self.assertIn("claim:change", reasons)
            self.assertIn("verify:ok", reasons)


if __name__ == "__main__":
    unittest.main()
