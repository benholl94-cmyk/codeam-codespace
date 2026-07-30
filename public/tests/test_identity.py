"""Tests for the unified pseudonym-identity system.

Covers:
  * Pseudonym determinism — same inputs -> same token
  * Pseudonym uniqueness — different inputs -> different tokens
  * IdentityChain.append + verify round-trip
  * Tampering detection — flipping a byte breaks the chain
  * record_conflict links to the active chain hash
  * RESTRICTIONS is non-empty + names hard world limits
  * CLI surface: identity {init,show,verify,conflict,restrictions,set-seed}
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str, state_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollout_shield", *args,
         "--state-root", str(state_root)],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )


class TestPseudonym(unittest.TestCase):
    def test_deterministic(self):
        from rollout_shield.identity import Pseudonym
        a = Pseudonym.derive(
            user_seed="seed-1", model_id="m1", session_id="s1",
            created_at=1000, prev_chain_hash="0" * 64,
        )
        b = Pseudonym.derive(
            user_seed="seed-1", model_id="m1", session_id="s1",
            created_at=1000, prev_chain_hash="0" * 64,
        )
        self.assertEqual(a.token, b.token)
        self.assertTrue(a.token.startswith("psn_"))

    def test_input_changes_change_token(self):
        from rollout_shield.identity import Pseudonym
        base = dict(user_seed="seed", model_id="m1", session_id="s1",
                    created_at=1000, prev_chain_hash="0" * 64)
        a = Pseudonym.derive(**base)
        for changed in (
            {"user_seed": "seed2"},
            {"model_id": "m2"},
            {"session_id": "s2"},
            {"prev_chain_hash": "f" * 64},
        ):
            b = Pseudonym.derive(**{**base, **changed})
            self.assertNotEqual(a.token, b.token,
                                f"changing {changed} should change token")

    def test_no_pii_in_token(self):
        """The token must never leak the seed verbatim."""
        from rollout_shield.identity import Pseudonym
        p = Pseudonym.derive(
            user_seed="a-very-long-and-obvious-user-seed-12345678",
            model_id="m", session_id="s",
            created_at=1000, prev_chain_hash="0" * 64,
        )
        self.assertNotIn("a-very-long-and-obvious-user-seed-12345678", p.token)


class TestIdentityChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-idtest-")
        self.root = Path(self.tmp)
        # seed file required so the chain can produce stable IDs
        (self.root / "identity").mkdir(parents=True, exist_ok=True)
        (self.root / "identity" / "seed").write_text("test-seed")

    def test_append_and_verify(self):
        from rollout_shield.identity import Pseudonym, IdentityChain
        chain = IdentityChain(self.root)
        p1 = Pseudonym.derive(user_seed="x", model_id="m",
                              session_id="s1", created_at=1,
                              prev_chain_hash="0" * 64)
        p1, h1 = chain.append(p1)
        p2 = Pseudonym.derive(user_seed="x", model_id="m",
                              session_id="s2", created_at=2,
                              prev_chain_hash=h1)
        p2, h2 = chain.append(p2)
        ok, errors = chain.verify()
        self.assertTrue(ok, msg=errors)
        self.assertEqual(p1.chain_id, "idc_000001")
        self.assertEqual(p2.chain_id, "idc_000002")

    def test_tamper_detected(self):
        from rollout_shield.identity import Pseudonym, IdentityChain
        chain = IdentityChain(self.root)
        p = Pseudonym.derive(user_seed="x", model_id="m", session_id="s",
                              created_at=1, prev_chain_hash="0" * 64)
        chain.append(p)
        # flip a byte in the on-disk record
        path = chain.file
        text = path.read_text()
        # replace first non-whitespace char with something different
        self.assertGreater(len(text), 10)
        path.write_text("x" + text[1:])
        ok, errors = chain.verify()
        self.assertFalse(ok)
        self.assertGreater(len(errors), 0)

    def test_chain_file_is_owner_only(self):
        from rollout_shield.identity import Pseudonym, IdentityChain
        chain = IdentityChain(self.root)
        p = Pseudonym.derive(user_seed="x", model_id="m", session_id="s",
                              created_at=1, prev_chain_hash="0" * 64)
        chain.append(p)
        mode = chain.file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600,
                         f"chain file should be 0600, got {oct(mode)}")


class TestConflictRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-conf-")
        self.root = Path(self.tmp)

    def test_links_to_active_chain(self):
        from rollout_shield.identity import (
            Pseudonym, IdentityChain, record_conflict,
        )
        chain = IdentityChain(self.root)
        p = Pseudonym.derive(user_seed="x", model_id="m", session_id="s",
                              created_at=1, prev_chain_hash="0" * 64)
        p, tip = chain.append(p)
        rec = record_conflict(
            self.root, pseudonym=p.token,
            user_says="do A", ai_understood="do B",
            resolution="use B",
        )
        self.assertEqual(rec.prev_chain_hash, tip)
        self.assertTrue(rec.conflict_id.startswith("cfl_"))
        # chain_hash must be deterministic SHA256 of prev + canonical
        canon = json.dumps({k: v for k, v in rec.to_dict().items()
                            if k != "chain_hash"},
                           sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(
            rec.prev_chain_hash.encode("ascii") + canon.encode("ascii")
        ).hexdigest()
        self.assertEqual(rec.chain_hash, expected)


class TestRestrictions(unittest.TestCase):
    def test_non_empty_and_well_formed(self):
        from rollout_shield.identity import RESTRICTIONS
        self.assertGreater(len(RESTRICTIONS), 0)
        for name, desc in RESTRICTIONS:
            self.assertIsInstance(name, str)
            self.assertIsInstance(desc, str)
            self.assertGreater(len(name), 3)
            self.assertGreater(len(desc), 20)

    def test_covers_expected_world_limits(self):
        from rollout_shield.identity import RESTRICTIONS
        names = {n for n, _ in RESTRICTIONS}
        for required in (
            "no_credential_theft",
            "no_targeted_harassment",
            "no_csam",
            "no_wmd_assistance",
            "no_platform_circumvention",
            "no_secrets_in_logs",
        ):
            self.assertIn(required, names,
                          f"missing world-restriction {required!r}")


class TestIdentityCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-cli-id-")
        self.root = Path(self.tmp)

    def test_restrictions_help_and_run(self):
        r = _run_cli("identity", "restrictions", state_root=self.root)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("no_credential_theft", r.stdout)

    def test_set_seed_creates_0600_file(self):
        r = _run_cli("identity", "set-seed", "cli-test-seed",
                     state_root=self.root)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        seed_file = self.root / "identity" / "seed"
        self.assertTrue(seed_file.exists())
        mode = seed_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_init_show_verify_round_trip(self):
        r1 = _run_cli("identity", "init", "--note", "hello",
                      state_root=self.root)
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        self.assertIn("pseudonym:", r1.stdout)
        self.assertIn("psn_", r1.stdout)

        r2 = _run_cli("identity", "show", state_root=self.root)
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        self.assertIn("psn_", r2.stdout)
        self.assertIn("hello", r2.stdout)

        r3 = _run_cli("identity", "verify", state_root=self.root)
        self.assertEqual(r3.returncode, 0, msg=r3.stderr)
        self.assertIn("OK", r3.stdout)

    def test_init_show_json(self):
        _run_cli("identity", "init", state_root=self.root)
        r = _run_cli("identity", "show", "--json", state_root=self.root)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("latest", data)
        self.assertIn("pseudonym", data["latest"])
        self.assertTrue(data["latest"]["pseudonym"].startswith("psn_"))

    def test_conflict_appends(self):
        _run_cli("identity", "init", state_root=self.root)
        r = _run_cli(
            "identity", "conflict",
            "--user-says", "build X",
            "--ai-understood", "build X with foo",
            "--resolution", "use foo",
            state_root=self.root,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("cfl_", r.stdout)
        # verify chain still OK after conflict
        r2 = _run_cli("identity", "verify", state_root=self.root)
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)

    def test_empty_chain_show_returns_1(self):
        r = _run_cli("identity", "show", state_root=self.root)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()