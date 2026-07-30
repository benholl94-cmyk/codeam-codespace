"""Tests for the one-way access gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestIsInternalAuthorized(unittest.TestCase):
    def test_user_actor_authorized(self):
        from rollout_shield.unique import is_internal_authorized
        self.assertTrue(is_internal_authorized("user:benholl94-cmyk"))
        self.assertTrue(is_internal_authorized("user:operator"))

    def test_model_actor_authorized(self):
        from rollout_shield.unique import is_internal_authorized
        self.assertTrue(is_internal_authorized("model:MiniMax-M3"))
        self.assertTrue(is_internal_authorized("model:claude-opus-4-8"))

    def test_agent_actor_authorized(self):
        from rollout_shield.unique import is_internal_authorized
        self.assertTrue(is_internal_authorized("agent:default"))
        self.assertTrue(is_internal_authorized("agent:cli-verify"))

    def test_external_denied(self):
        from rollout_shield.unique import is_internal_authorized
        for bad in (
            "external",
            "public",
            "unknown",
            "user:",  # empty handle
            ":benholl94-cmyk",  # empty prefix
            "USER:benholl94-cmyk",  # wrong case
            "user:bad space",
            "user:bad/slash",
            "user:" + "x" * 100,  # too long
            "",
            None,
            42,
        ):
            self.assertFalse(is_internal_authorized(bad),
                             f"{bad!r} should be denied")


class TestOneWayGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-unique-")
        self.root = Path(self.tmp)

    def test_internal_path_refused_even_for_authorized_actor(self):
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        with self.assertRaises(PermissionError):
            gate.require_authorization(
                actor="user:operator",
                intent="read keys_material",
                path=self.root / "keys_material" / "agent_default.pem",
            )

    def test_external_actor_refused(self):
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        with self.assertRaises(PermissionError):
            gate.require_authorization(
                actor="external",
                intent="read state",
                path=self.root / "state" / "claims.jsonl",
            )

    def test_authorized_actor_no_path_passes(self):
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        # No path -> no path boundary to cross.
        gate.require_authorization(
            actor="model:MiniMax-M3",
            intent="list audit log",
        )

    def test_external_safe_path_denied_for_actor(self):
        """Even a non-internal path is refused if the actor is external."""
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        with self.assertRaises(PermissionError):
            gate.require_authorization(
                actor="public",
                intent="read public docs",
                path=self.root / "docs" / "IDENTITY.md",
            )

    def test_traversal_attack_refused(self):
        """Even with ../ in the path, internal-prefix check still fires."""
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        sneaky = self.root / "docs" / ".." / "keys_material" / "x.pem"
        with self.assertRaises(PermissionError):
            gate.require_authorization(
                actor="user:operator",
                intent="traversal probe",
                path=sneaky,
            )

    def test_check_path_returns_bool(self):
        from rollout_shield.unique import OneWayGate
        gate = OneWayGate(state_root=self.root)
        # Use a path that even an authorized actor couldn't traverse;
        # check_path uses user:probe to exercise only the path side.
        internal = self.root / "identity" / "chain.jsonl"
        self.assertFalse(gate.check_path(internal))
        safe = self.root / "docs" / "IDENTITY.md"
        # Note: check_path uses actor=user:probe to bypass the actor
        # check and exercise the path check; the path itself is safe.
        self.assertTrue(gate.check_path(safe))


class TestAuthorizedActorsFromChain(unittest.TestCase):
    def test_empty_chain_returns_seed_actors(self):
        from rollout_shield.unique import authorized_actors_from_chain
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "identity").mkdir(parents=True)
            (root / "identity" / "seed").write_text("x")
            actors = authorized_actors_from_chain(root)
            self.assertIn("user:operator", actors)
            self.assertIn("model:MiniMax-M3", actors)

    def test_chain_with_model_id_adds_model_actor(self):
        from rollout_shield.unique import authorized_actors_from_chain
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "identity").mkdir(parents=True)
            chain = root / "identity" / "chain.jsonl"
            chain.write_text(json.dumps({
                "chain_id": "idc_000001",
                "model_id": "MiniMax-M3",
                "pseudonym": "psn_test",
                "chain_hash": "0" * 64,
            }) + "\n")
            actors = authorized_actors_from_chain(root)
            self.assertIn("model:MiniMax-M3", actors)


if __name__ == "__main__":
    unittest.main()