"""Smoke tests — does the package import, can every subcommand respond to
--help, and does self-check pass overall?"""
import json
import subprocess
import sys
import unittest


class TestPackageImport(unittest.TestCase):
    def test_rollout_shield_imports(self):
        import rollout_shield  # noqa: F401
        self.assertTrue(hasattr(rollout_shield, "__file__"))

    def test_subcommands_importable(self):
        from rollout_shield import (
            alerter,
            cli,
            health_checks,
            http_server,
            monitor_daemon,
            state,
        )
        for mod in (alerter, cli, health_checks, http_server, monitor_daemon, state):
            self.assertTrue(hasattr(mod, "__file__"))

    def test_command_subpackages_importable(self):
        from rollout_shield.commands import claim, keys, reputation, self_check, verify
        for mod in (claim, keys, reputation, self_check, verify):
            self.assertTrue(hasattr(mod, "__file__"))


class TestCLI(unittest.TestCase):
    """All 9 subcommands must respond to ``--help`` without erroring."""

    COMMANDS = ("install", "status", "claim", "verify", "monitor",
                "dashboard", "reputation", "self-check", "keys")

    def test_every_subcommand_has_help(self):
        for cmd in self.COMMANDS:
            with self.subTest(cmd=cmd):
                r = subprocess.run(
                    [sys.executable, "-m", "rollout_shield", cmd, "--help"],
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(r.returncode, 0,
                                 f"{cmd} --help failed: {r.stderr!r}")

    def test_self_check_passes(self):
        r = subprocess.run(
            [sys.executable, "-m", "rollout_shield", "self-check", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data.get("overall_ok"), data)
        failing = [k for k, v in data.get("checks", {}).items() if not v.get("ok")]
        self.assertEqual(failing, [], f"failing components: {failing}")


if __name__ == "__main__":
    unittest.main()
