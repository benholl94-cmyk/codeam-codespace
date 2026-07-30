"""Tests for the pip-installable package and OWNER-FIRST license presence."""
from __future__ import annotations

import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestPyprojectMetadata(unittest.TestCase):
    """pyproject.toml declares a buildable, installable, runnable package."""

    def test_pyproject_exists_and_parses(self):
        p = ROOT / "pyproject.toml"
        self.assertTrue(p.exists(), "pyproject.toml missing")
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["name"], "rollout-shield")
        self.assertEqual(data["project"]["version"], "0.3.0")
        # entry point
        scripts = data["project"]["scripts"]
        self.assertIn("rollout-shield", scripts)
        self.assertEqual(scripts["rollout-shield"], "rollout_shield.cli:main")
        # deps
        deps = data["project"]["dependencies"]
        self.assertIn("cryptography>=41.0.0", deps)

    def test_python_version_floor(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["requires-python"], ">=3.10")

    def test_py_typed_marker(self):
        self.assertTrue((ROOT / "rollout_shield" / "py.typed").exists(),
                        "py.typed marker missing")


class TestEntryPointImport(unittest.TestCase):
    """The CLI entry point is importable and has a main() function."""

    def test_cli_main_callable(self):
        from rollout_shield import cli
        self.assertTrue(callable(cli.main))


class TestOwnerFirstLicense(unittest.TestCase):
    """OWNER-FIRST-LICENSE.md is present and has the required sections."""

    def setUp(self):
        self.path = ROOT / "OWNER-FIRST-LICENSE.md"
        self.text = self.path.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_required_sections(self):
        for required in ("Owner's rights", "Third-party prohibitions",
                         "What the Work never does",
                         "verified absence of", "Encrypted at rest",
                         "Tamper-evident", "Loopback by default",
                         "Owner-unlock gating"):
            self.assertIn(required, self.text,
                          f"missing section/clause: {required}")

    def test_no_outbound_network_clause(self):
        # The Work never initiates a connection to a host the Owner
        # has not explicitly configured
        self.assertIn("No outbound network", self.text)

    def test_owner_uniqueness(self):
        # Owner is identified by git-committer identity at first install
        self.assertIn("git-committer identity", self.text)


class TestEntryPointRuns(unittest.TestCase):
    """`rollout-shield --version` works (smoke test for the console script)."""

    def test_help_exits_0(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "rollout_shield.cli", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("0.3.0", r.stdout)

    def test_subcommand_help_exits_0(self):
        import subprocess
        for sub in ("install", "status", "claim", "verify", "dashboard",
                    "monitor", "reputation", "self-check", "deploy"):
            r = subprocess.run(
                [sys.executable, "-m", "rollout_shield.cli", sub, "--help"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0, msg=f"{sub}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
