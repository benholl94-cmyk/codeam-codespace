"""End-to-end key generation → claim creation → signature verification."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestClaimRoundTrip(unittest.TestCase):
    def test_key_then_claim_then_verify(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env_root = str(root)
            # 1) generate a key for an agent
            r1 = subprocess.run(
                [sys.executable, "-m", "rollout_shield", "keys", "new",
                 "--agent-id", "test-agent", "--state-root", env_root],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)

            # 2) create a signed claim
            r2 = subprocess.run(
                [sys.executable, "-m", "rollout_shield", "claim", "create",
                 "--agent-id", "test-agent",
                 "--type", "change",
                 "--body", "test claim body",
                 "--state-root", env_root],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)
            # claim id appears in the "created key" or "claim created:" line
            cid = None
            for line in r2.stdout.splitlines():
                if line.startswith("claim created:"):
                    cid = line.split()[2]
                    break
            self.assertIsNotNone(cid, f"claim id not found in: {r2.stdout!r}")

            # 3) verify the freshly-signed claim
            r3 = subprocess.run(
                [sys.executable, "-m", "rollout_shield", "verify",
                 cid, "--state-root", env_root],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r3.returncode, 0, msg=r3.stderr)
            # Verify output should start with "OK"
            self.assertTrue(r3.stdout.startswith("OK"), r3.stdout)


if __name__ == "__main__":
    unittest.main()
