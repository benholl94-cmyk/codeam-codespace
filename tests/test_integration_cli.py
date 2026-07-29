"""Integration tests — exercise the CLI as a subprocess against a scratch state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


CLI = Path.home() / "usr" / "bin" / "rollout-shield"
HAS_INSTALL = CLI.exists()


def _run_cli(args: list[str], state_root: Path, timeout: float = 30.0) -> dict:
    """Run the CLI; return rc + stdout + stderr."""
    cmd = [str(CLI), "--state-root", str(state_root)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=False)
    return {"rc": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr, "ok": proc.returncode == 0}


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
def test_cli_install_creates_state(scratch_state_root):
    r = _run_cli(["install"], scratch_state_root)
    assert r["ok"], r["stderr"]
    assert (scratch_state_root / "config.json").exists()


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
def test_cli_status_reports_state(scratch_state_root):
    _run_cli(["install"], scratch_state_root)
    r = _run_cli(["status", "--json"], scratch_state_root)
    assert r["ok"]
    payload = json.loads(r["stdout"])
    assert payload["state_root"] == str(scratch_state_root)


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
@pytest.mark.requires_cryptography
def test_cli_keys_new_then_list(scratch_state_root, requires_cryptography):
    _run_cli(["install"], scratch_state_root)
    r = _run_cli(["keys", "new", "--agent-id", "test-a"], scratch_state_root)
    assert r["ok"], r["stderr"]
    r = _run_cli(["keys", "list", "--json"], scratch_state_root)
    assert r["ok"]
    payload = json.loads(r["stdout"])
    assert len(payload["keys"]) == 1
    assert payload["keys"][0]["agent_id"] == "test-a"


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
def test_cli_space_show_default_shared(scratch_state_root):
    _run_cli(["install"], scratch_state_root)
    r = _run_cli(["space", "show", "--json"], scratch_state_root)
    assert r["ok"]
    payload = json.loads(r["stdout"])
    assert payload["policy"] == "shared"


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
def test_cli_space_set_policy_then_validate(scratch_state_root):
    _run_cli(["install"], scratch_state_root)
    r = _run_cli(["space", "set-policy", "device-only", "--yes"], scratch_state_root)
    assert r["ok"], r["stderr"]
    r = _run_cli(["space", "show", "--json"], scratch_state_root)
    assert r["ok"]
    assert json.loads(r["stdout"])["policy"] == "device-only"
    # restore
    _run_cli(["space", "set-policy", "shared", "--yes"], scratch_state_root)


@pytest.mark.skipif(not HAS_INSTALL, reason="scripts/install.sh has not been run")
@pytest.mark.slow
def test_cli_self_test_pass(scratch_state_root):
    """End-to-end smoke test using the installed CLI."""
    r = _run_cli(["self-test"], scratch_state_root, timeout=60.0)
    assert r["ok"], r["stderr"]
    assert "ALL GREEN" in r["stdout"]
