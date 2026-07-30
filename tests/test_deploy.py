"""Tests for the deploy bundle generator and bounded middleware."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rollout_shield import deploy as _deploy


class TestTokenBucket(unittest.TestCase):
    """Per-IP token bucket — bounded rate limiter."""

    def test_initial_burst_allowed(self):
        b = _deploy.TokenBucket(capacity=5, refill_per_sec=0.0)  # no refill
        for _ in range(5):
            self.assertTrue(b.take("1.2.3.4", now=100.0))
        # 6th must fail
        self.assertFalse(b.take("1.2.3.4", now=100.0))

    def test_refill_after_time(self):
        b = _deploy.TokenBucket(capacity=5, refill_per_sec=1.0)
        # drain
        for _ in range(5):
            b.take("ip", now=100.0)
        self.assertFalse(b.take("ip", now=100.0))
        # 1 sec later: 1 token available
        self.assertTrue(b.take("ip", now=101.0))
        # immediately after: empty again
        self.assertFalse(b.take("ip", now=101.0))

    def test_per_ip_isolation(self):
        b = _deploy.TokenBucket(capacity=2, refill_per_sec=0.0)
        self.assertTrue(b.take("a", now=100.0))
        self.assertTrue(b.take("a", now=100.0))
        self.assertFalse(b.take("a", now=100.0))
        # 'b' has its own bucket
        self.assertTrue(b.take("b", now=100.0))
        self.assertTrue(b.take("b", now=100.0))

    def test_evict_idle(self):
        b = _deploy.TokenBucket(capacity=2, refill_per_sec=0.0)
        b.take("old", now=100.0)
        b.take("new", now=200.0)
        evicted = b.evict_idle(max_age=10.0, now=200.0)
        self.assertEqual(evicted, 1)
        self.assertNotIn("old", b.buckets)
        self.assertIn("new", b.buckets)


class TestSecurityHeaders(unittest.TestCase):
    """Security headers are applied idempotently."""

    def test_adds_all_headers(self):
        out = _deploy.apply_security_headers([])
        keys = {k.lower() for k, _ in out}
        for required in ("content-security-policy", "strict-transport-security",
                         "x-frame-options", "x-content-type-options",
                         "referrer-policy", "permissions-policy"):
            self.assertIn(required, keys)

    def test_replaces_existing(self):
        out = _deploy.apply_security_headers([("X-Frame-Options", "SAMEORIGIN")])
        xfo = [v for k, v in out if k.lower() == "x-frame-options"]
        self.assertEqual(xfo, ["DENY"])

    def test_preserves_other_headers(self):
        out = _deploy.apply_security_headers([("X-Custom", "abc")])
        self.assertIn(("X-Custom", "abc"), out)


class TestBundleAssembly(unittest.TestCase):
    """The bundle contains exactly the runtime minimum, never the source repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deploy-test-")
        self.src = Path(self.tmp) / "src"
        self.src.mkdir()
        # fake source layout
        (self.src / "rollout_shield").mkdir()
        for name in ("__init__.py", "http_server.py", "state.py"):
            (self.src / "rollout_shield" / name).write_text(f"# {name}\n")
        (self.src / "rollout_shield" / "interface").mkdir()
        (self.src / "rollout_shield" / "interface" / "index.html").write_text("<html/>")
        self.out = Path(self.tmp) / "bundle"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundle_lays_out_required_files(self):
        info = _deploy.assemble_bundle(self.out, self.src)
        self.assertEqual(info["version"], _deploy.BUNDLE_VERSION)
        for required in ("Dockerfile", "docker-compose.yml", "nginx.conf",
                         "VERSION", "README.md", "scripts/run.sh",
                         "scripts/init-unlock.sh", "scripts/backup.sh",
                         "scripts/healthcheck.sh",
                         "rollout_shield/__init__.py",
                         "rollout_shield/http_server.py",
                         "rollout_shield/state.py"):
            self.assertTrue((self.out / required).exists(),
                            f"missing {required}")

    def test_bundle_does_not_include_source_repo(self):
        _deploy.assemble_bundle(self.out, self.src)
        # No tests, no docs, no protocol/, no tools/, no monitoring/, no agent/
        for forbidden in ("tests", "tools", "docs", "protocol",
                          "monitoring", "agent", ".git", "CHANGELOG.md",
                          "SELF_DIAGNOSIS.md"):
            self.assertFalse((self.out / forbidden).exists(),
                             f"bundle must NOT contain {forbidden}")

    def test_manifest_has_sha256_for_every_file(self):
        _deploy.assemble_bundle(self.out, self.src)
        manifest = json.loads((self.out / "MANIFEST.json").read_text())
        self.assertEqual(manifest["version"], _deploy.BUNDLE_VERSION)
        for rel, sha in manifest["files"].items():
            p = self.out / rel
            self.assertTrue(p.exists(), f"manifest lists missing file: {rel}")
            import hashlib
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(h, sha, f"sha256 mismatch: {rel}")

    def test_pack_tarball_round_trip(self):
        _deploy.assemble_bundle(self.out, self.src)
        tarball = Path(self.tmp) / "bundle.tar.gz"
        n = _deploy.pack_tarball(self.out, tarball)
        self.assertTrue(tarball.exists())
        self.assertGreater(tarball.stat().st_size, 0)
        self.assertGreater(n, 0)
        import tarfile
        with tarfile.open(tarball) as tf:
            names = tf.getnames()
        self.assertIn("Dockerfile", names)
        self.assertIn("MANIFEST.json", names)


class TestCLIIntegration(unittest.TestCase):
    """The `rollout-shield deploy` CLI subcommand works end-to-end."""

    def test_bundle_then_check(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            bundle_dir = td / "bundle"
            tarball = td / "bundle.tar.gz"
            r = subprocess.run(
                [sys.executable, "-m", "rollout_shield.cli",
                 "deploy", "bundle",
                 "--out", str(bundle_dir),
                 "--tarball", str(tarball)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertTrue(bundle_dir.exists())
            self.assertTrue(tarball.exists())
            # verify
            r2 = subprocess.run(
                [sys.executable, "-m", "rollout_shield.cli",
                 "deploy", "check", "--bundle", str(bundle_dir)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)
            self.assertIn("all sha256 match", r2.stdout)


if __name__ == "__main__":
    unittest.main()
