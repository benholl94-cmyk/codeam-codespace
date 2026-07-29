"""Smoke tests for the webhook delivery subsystem.

These tests run against the INSTALLED CLI (``~/usr/bin/rollout-shield``
or ``~/.local/bin/rollout-shield``) end-to-end. They are skipped if no
CLI is installed.

Marked with ``@pytest.mark.smoke`` so ``pytest -m smoke`` runs them.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_cli() -> str | None:
    env = os.environ.get("ROLLOUT_SHIELD_CLI")
    if env and os.path.isfile(env):
        return env
    for c in [
        Path.home() / "usr" / "bin" / "rollout-shield",
        Path.home() / ".local" / "bin" / "rollout-shield",
        REPO_ROOT / "bin" / "rollout-shield",
    ]:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c.resolve())
    return None


CLI = _find_cli()
needs_cli = pytest.mark.skipif(
    CLI is None,
    reason="rollout-shield CLI not installed (run bash scripts/install.sh)",
)


@pytest.fixture
def scratch_state_root():
    root = Path(tempfile.mkdtemp(prefix="rollout-shield-wh-smoke-"))
    try:
        yield root
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def _run(args: list[str], state_root: Path, timeout: float = 30.0) -> dict:
    proc = subprocess.run(
        [CLI, "--state-root", str(state_root)] + args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return {
        "rc": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "ok": proc.returncode == 0,
    }


@needs_cli
@pytest.mark.smoke
def test_cli_target_add_list_remove(scratch_state_root):
    r1 = _run(["webhooks", "target", "add", "smoke1",
               "https://example.com/hook",
               "--sign-mode", "hmac", "--signing-key", "secret"],
              scratch_state_root)
    assert r1["ok"], r1["stderr"]

    r2 = _run(["webhooks", "target", "list", "--json"], scratch_state_root)
    assert r2["ok"]
    targets = json.loads(r2["stdout"])
    names = [t["name"] for t in targets]
    assert "smoke1" in names

    r3 = _run(["webhooks", "target", "remove", "smoke1"], scratch_state_root)
    assert r3["ok"]


@needs_cli
@pytest.mark.smoke
def test_cli_end_to_end_against_mock_receiver(scratch_state_root):
    """Spin up a mock receiver, deliver via CLI, drain, verify delivered."""
    received: list[dict] = []
    lock = threading.Lock()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            with lock:
                received.append({"headers": dict(self.headers), "body": body})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_a):  # noqa: N802
            return

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Register target
        r1 = _run(["webhooks", "target", "add", "smoke-recv",
                   f"http://127.0.0.1:{port}/hook",
                   "--sign-mode", "hmac", "--signing-key", "smoke-secret"],
                  scratch_state_root)
        assert r1["ok"], r1["stderr"]

        # Deliver
        payload = json.dumps({"event": "smoke", "ts": int(time.time())})
        r2 = _run(["webhooks", "deliver", "--target", "smoke-recv",
                   "--payload", payload, "--json"],
                  scratch_state_root)
        assert r2["ok"], r2["stderr"]
        rec = json.loads(r2["stdout"])
        delivery_id = rec["enqueued"]["delivery_id"]

        # Drain
        r3 = _run(["webhooks", "drain"], scratch_state_root, timeout=15.0)
        assert r3["ok"], r3["stderr"]

        # Deliveries list — should show delivered
        r4 = _run(["webhooks", "deliveries", "list", "--json"],
                  scratch_state_root)
        assert r4["ok"]
        deliveries = json.loads(r4["stdout"])
        target_del = next((d for d in deliveries
                           if d["delivery_id"] == delivery_id), None)
        assert target_del is not None
        assert target_del["status"] == "delivered"

        # Stats — should reflect at least one delivery
        r5 = _run(["webhooks", "stats", "--json"], scratch_state_root)
        assert r5["ok"]
        stats = json.loads(r5["stdout"])
        assert stats["delivered_total"] >= 1

        # Mock receiver received at least one request with HMAC header
        with lock:
            assert len(received) >= 1
            assert received[-1]["headers"].get(
                "X-Rollout-Shield-Signature", "").startswith("sha256=")
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001
            pass


@needs_cli
@pytest.mark.smoke
def test_cli_dlq_and_replay(scratch_state_root):
    """Mock always 500 -> delivery goes to DLQ; replay re-enqueues."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_a):  # noqa: N802
            return

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _run(["webhooks", "target", "add", "fail-recv",
              f"http://127.0.0.1:{port}/hook",
              "--sign-mode", "none", "--max-attempts", "2"],
             scratch_state_root)
        r1 = _run(["webhooks", "deliver", "--target", "fail-recv",
                   "--payload", json.dumps({"e": "dlq"}), "--json"],
                  scratch_state_root)
        assert r1["ok"], r1["stderr"]
        delivery_id = json.loads(r1["stdout"])["enqueued"]["delivery_id"]

        # Drain twice — should DLQ after 2 attempts
        for _ in range(3):
            drain = _run(["webhooks", "drain"], scratch_state_root, timeout=15.0)
            assert drain["ok"]
        r2 = _run(["webhooks", "deliveries", "show", delivery_id, "--json"],
                  scratch_state_root)
        assert r2["ok"]
        rec = json.loads(r2["stdout"])
        assert rec["status"] == "dlq"

        # Replay
        r3 = _run(["webhooks", "replay", delivery_id, "--json"], scratch_state_root)
        assert r3["ok"], r3["stderr"]
        assert json.loads(r3["stdout"])["replayed"]["status"] == "pending"

        # Stats show replayed_total >= 1
        r4 = _run(["webhooks", "stats", "--json"], scratch_state_root)
        assert r4["ok"]
        assert json.loads(r4["stdout"])["replayed_total"] >= 1
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
