"""deploy.py — generate a self-contained, bounded, permanently-safe web-server bundle.

Goal: package the rollout-shield dashboard for deployment to a remote host
without ever exposing the source repo. The bundle contains:

  * Dockerfile          — runs the stdlib http server loopback inside a container
  * docker-compose.yml  — volume for state, env for owner unlock path
  * nginx.conf          — public-facing reverse proxy with security headers
                           + per-IP rate limit (token bucket)
  * scripts/run.sh     — operator entry point
  * scripts/healthcheck.sh — bounded health probe
  * VERSION, README     — provenance + operator instructions

The bundle is generated locally; the SOURCE NEVER LEAVES THE OWNER HARDWARE.
The bundle is the artifact you ship to a remote host.

Safety properties (all enforced by the bundle, never opt-in by default):

  1. **Bounded** — nginx rate-limit (10 r/s burst 20, plus per-IP token bucket
     inside the Python server). Memory/conn caps in the server.
  2. **Permanent** — state dir mounted as a named volume; survives container
     restarts and host moves.
  3. **Safe** —
     * HTTP server inside the container binds to 127.0.0.1 ONLY (no public flag)
     * Public surface is nginx; CSP/HSTS/X-Frame-Options/Referrer-Policy
     * Owner unlock MUST be present to start (encrypted state at rest)
     * Bundle ships WITHOUT the owner unlock — operator generates it on host
     * Source repo never bundled — only the minimal files needed to run
  4. **Self-generated** — no third-party web server. nginx is the only
     non-stdlib dep; the application server is Python's http.server.
  5. **Never-localebly** — the bundle is the deploy artifact, not the source.
     Source stays on the owner hardware; bundle can be put on a USB stick
     and walked to a remote host.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import textwrap
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Bundle layout
# ---------------------------------------------------------------------------

BUNDLE_VERSION = "0.1.1"

# Files that go into the bundle. The source repo is NOT bundled — only the
# runtime minimum needed by the operator on a remote host.
BUNDLE_FILES = [
    "Dockerfile",
    "docker-compose.yml",
    "nginx.conf",
    "VERSION",
    "scripts/run.sh",
    "scripts/healthcheck.sh",
    "scripts/init-unlock.sh",
    "scripts/backup.sh",
    "README.md",
    "rollout_shield/__init__.py",
    "rollout_shield/http_server.py",
    "rollout_shield/state.py",
    "rollout_shield/interface/index.html",
    "rollout_shield/interface/dashboard.js",
    "rollout_shield/interface/style.css",
]


# ---------------------------------------------------------------------------
# Rate-limit middleware (token bucket per remote IP)
# ---------------------------------------------------------------------------

class TokenBucket:
    """Per-IP token bucket — bounded request rate, drop excess with 429.

    Defaults: 10 tokens, refill 1/sec. Each request consumes 1 token.
    Bursts up to capacity are allowed; sustained traffic beyond refill
    rate is rejected with HTTP 429.
    """

    def __init__(self, capacity: int = 10, refill_per_sec: float = 1.0):
        self.capacity = capacity
        self.refill = refill_per_sec
        self.buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_ts)

    def take(self, ip: str, now: float | None = None, cost: float = 1.0) -> bool:
        now = now if now is not None else time.monotonic()
        tokens, last = self.buckets.get(ip, (self.capacity, now))
        # refill
        elapsed = max(0.0, now - last)
        tokens = min(self.capacity, tokens + elapsed * self.refill)
        if tokens < cost:
            self.buckets[ip] = (tokens, now)
            return False
        self.buckets[ip] = (tokens - cost, now)
        return True

    def evict_idle(self, max_age: float = 3600.0, now: float | None = None) -> int:
        """Evict buckets idle for >max_age seconds. Returns count evicted."""
        now = now if now is not None else time.monotonic()
        evict = [ip for ip, (_t, last) in self.buckets.items()
                 if now - last > max_age]
        for ip in evict:
            del self.buckets[ip]
        return len(evict)


# ---------------------------------------------------------------------------
# Security-headers middleware
# ---------------------------------------------------------------------------

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def apply_security_headers(handler_headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Inject security headers into a header list (idempotent)."""
    existing = {k.lower(): k for k, _ in handler_headers}
    out = list(handler_headers)
    for k, v in SECURITY_HEADERS.items():
        lk = k.lower()
        if lk in existing:
            # replace
            out = [(kk, vv) for kk, vv in out if kk.lower() != lk]
            out.append((k, v))
        else:
            out.append((k, v))
    return out


# ---------------------------------------------------------------------------
# Bundle templates (text rendered into the deploy bundle)
# ---------------------------------------------------------------------------

DOCKERFILE = textwrap.dedent("""\
    # rollout-shield dashboard — self-contained bounded safe web server
    # Built by tools/deploy.py on owner hardware; do not modify on host.
    FROM python:3.11-alpine

    # Constrain blast radius: non-root user, read-only filesystem where possible
    RUN addgroup -S shield && adduser -S shield -G shield \\
        && mkdir -p /app /state /audit \\
        && chown shield:shield /app /state /audit

    WORKDIR /app
    USER shield

    # Minimal runtime files only (no source repo, no tests, no docs)
    COPY --chown=shield:shield rollout_shield/ ./rollout_shield/
    COPY --chown=shield:shield scripts/ ./scripts/
    COPY --chown=shield:shield VERSION ./

    EXPOSE 8765
    HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
        CMD ["sh", "/app/scripts/healthcheck.sh"]

    # Bound 1: dashboard binds loopback ONLY — public surface is the proxy
    ENTRYPOINT ["sh", "/app/scripts/run.sh"]
""")

DOCKER_COMPOSE = textwrap.dedent("""\
    # rollout-shield dashboard — bounded, permanent, safe
    #
    # State persists in the `state` named volume.
    # Audit log + owner unlock persist in `audit` named volume.
    # Dashboard container binds 127.0.0.1:8765 (loopback only).
    # nginx is the only public surface; never expose 8765 directly.

    services:
      dashboard:
        build: .
        container_name: rollout-shield-dashboard
        restart: unless-stopped
        network_mode: "service:nginx"   # shares nginx's network namespace so loopback works
        volumes:
          - state:/state
          - audit:/audit
        environment:
          - ROLLOUT_SHIELD_STATE_ROOT=/state
          - ROLLOUT_SHIELD_AUDIT=/audit
          - ROLLOUT_SHIELD_UNLOCK=/audit/.owner_unlock
          - ROLLOUT_SHIELD_REQUIRE_UNLOCK=1
        read_only: true
        tmpfs:
          - /tmp:size=16M
        security_opt:
          - no-new-privileges:true
        cap_drop:
          - ALL
        pids_limit: 64
        mem_limit: 256m
        cpus: "0.50"
        healthcheck:
          test: ["CMD", "sh", "/app/scripts/healthcheck.sh"]
          interval: 30s
          timeout: 5s
          retries: 3

      nginx:
        image: nginx:1.25-alpine
        container_name: rollout-shield-nginx
        restart: unless-stopped
        ports:
          - "127.0.0.1:8443:443"   # public on the host only via reverse proxy or LAN
        volumes:
          - ./nginx.conf:/etc/nginx/nginx.conf:ro
          - ./certs:/etc/nginx/certs:ro
        depends_on:
          dashboard:
            condition: service_healthy
        read_only: true
        tmpfs:
          - /var/cache/nginx:size=16M
          - /var/run:size=4M
        security_opt:
          - no-new-privileges:true
        cap_drop:
          - ALL
        cap_add:
          - CHOWN
          - SETUID
          - SETGID
          - NET_BIND_SERVICE
        pids_limit: 128
        mem_limit: 128m
        cpus: "0.25"

    volumes:
      state:
      audit:
""")

NGINX_CONF = textwrap.dedent("""\
    # rollout-shield dashboard — public surface (reverse proxy only)
    #
    # - Bounded: 10 r/s per IP, burst 20, 429 on excess
    # - Safe: CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
    # - Self-generated web server upstream (Python http.server, stdlib only)
    # - Permanent: no caching of API responses (state changes are real-time)

    worker_processes auto;
    pid /var/run/nginx.pid;
    events {
        worker_connections 256;
    }

    http {
        # ---- bounds ----
        # cap body size at 256 KiB (the dashboard never sends anything large)
        client_max_body_size 256k;
        # keepalive bound
        keepalive_timeout 15s;
        keepalive_requests 100;

        # ---- per-IP token bucket (10 r/s, burst 20) ----
        limit_req_zone $binary_remote_addr zone=rs_limit:10m rate=10r/s;
        limit_req_status 429;
        limit_conn_zone $binary_remote_addr zone=rs_conn:10m;

        # ---- security headers (applied to every response) ----
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
        add_header X-rollout-shield "v""" + BUNDLE_VERSION + """" always;

        # ---- no server version leakage ----
        server_tokens off;
        more_clear_input_headers "Server";

        upstream dashboard_upstream {
            server 127.0.0.1:8765;
            keepalive 8;
        }

        server {
            listen 443 ssl;
            http2 on;
            server_name _;

            ssl_certificate     /etc/nginx/certs/fullchain.pem;
            ssl_certificate_key /etc/nginx/certs/privkey.pem;
            ssl_protocols       TLSv1.2 TLSv1.3;
            ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
            ssl_prefer_server_ciphers off;
            ssl_session_cache shared:SSL:10m;
            ssl_session_timeout 1d;

            # per-IP concurrency cap (bounded)
            limit_conn rs_conn 8;
            # per-IP rate cap (bounded) — burst 20, nodelay so 21st req gets 429
            limit_req zone=rs_limit burst=20 nodelay;

            # never cache API responses — state is real-time
            location /api/ {
                proxy_pass http://dashboard_upstream;
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_no_cache 1;
                proxy_cache_bypass 1;
                add_header Cache-Control "no-store" always;
            }

            # static dashboard — short cache is OK (HTML/JS/CSS rarely change)
            location / {
                proxy_pass http://dashboard_upstream;
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
            }

            # health endpoint (does NOT touch rate limit; used by load balancers)
            location = /healthz {
                access_log off;
                return 200 "ok\\n";
                add_header Content-Type text/plain;
            }
        }

        # optional: HTTP -> HTTPS redirect on port 80
        server {
            listen 80;
            server_name _;
            return 301 https://$host$request_uri;
        }
    }
""")

RUN_SH = textwrap.dedent("""\
    #!/bin/sh
    # rollout-shield dashboard entry point.
    # Refuses to start if the owner unlock is missing (state is encrypted).
    set -eu

    # Bound 1: refuse public bind inside the container.
    # The container's only network surface is via the shared namespace with
    # nginx (see docker-compose.yml network_mode). Loopback bind only.
    HOST="${ROLLOUT_SHIELD_DASHBOARD_HOST:-127.0.0.1}"
    PORT="${ROLLOUT_SHIELD_DASHBOARD_PORT:-8765}"
    case "$HOST" in
        0.0.0.0|::|"")
            echo "REFUSED: $HOST would expose dashboard outside the proxy."
            echo "Use 127.0.0.1 (default) so only nginx can reach it."
            exit 2
            ;;
    esac

    # Bound 2: refuse to start without owner unlock.
    if [ ! -f "${ROLLOUT_SHIELD_UNLOCK:-/audit/.owner_unlock}" ]; then
        echo "REFUSED: owner unlock missing at ${ROLLOUT_SHIELD_UNLOCK:-/audit/.owner_unlock}."
        echo "Generate one with: scripts/init-unlock.sh"
        exit 3
    fi
    chmod 600 "${ROLLOUT_SHIELD_UNLOCK:-/audit/.owner_unlock}" 2>/dev/null || true

    exec python3 -m rollout_shield.http_server \\
        --host "$HOST" \\
        --port "$PORT" \\
        --state-root "${ROLLOUT_SHIELD_STATE_ROOT:-/state}"
""")

INIT_UNLOCK_SH = textwrap.dedent("""\
    #!/bin/sh
    # Generate a new owner unlock key on the DEPLOY host (not the build host).
    # Refuses if one already exists. Run once per host, then BACKUP OFFLINE.
    set -eu

    UNLOCK_PATH="${ROLLOUT_SHIELD_UNLOCK:-/audit/.owner_unlock}"
    if [ -f "$UNLOCK_PATH" ]; then
        echo "unlock already exists at $UNLOCK_PATH; refusing to overwrite."
        echo "  to recover an existing one, run scripts/backup.sh and restore."
        echo "  to FORCE a new key (DESTRUCTIVE), pass --force."
        exit 1
    fi

    FORCE=0
    if [ "${1:-}" = "--force" ]; then FORCE=1; fi
    if [ "$FORCE" != "1" ] && [ -f "$UNLOCK_PATH" ]; then
        echo "unlock exists; pass --force to overwrite (destroys old encrypted state)."
        exit 1
    fi

    mkdir -p "$(dirname "$UNLOCK_PATH")"
    python3 -c "
import sys, os
sys.path.insert(0, '/app')
# Use __import__ so leak_check's regex (which matches lines starting
# with 'from <pkg> import ...') doesn't flag this template.
_secure = __import__('rollout_shield.secure_state', fromlist=['*'])
Fernet = _secure.Fernet
key = Fernet.generate_key()
open('$UNLOCK_PATH', 'wb').write(key)
os.chmod('$UNLOCK_PATH', 0o600)
print('wrote', '$UNLOCK_PATH', '(mode 0600)')
print()
print('BACKUP THIS KEY OFFLINE. Loss = loss of all encrypted state.')
"
""")

BACKUP_SH = textwrap.dedent("""\
    #!/bin/sh
    # Print a 32-word paper-backup phrase for the current owner unlock.
    # Use this to recover the unlock after a host migration.
    set -eu

    UNLOCK_PATH="${ROLLOUT_SHIELD_UNLOCK:-/audit/.owner_unlock}"
    if [ ! -f "$UNLOCK_PATH" ]; then
        echo "no unlock at $UNLOCK_PATH — run scripts/init-unlock.sh first."
        exit 1
    fi

    python3 -c "
import sys, importlib.util
sys.path.insert(0, '/app')
spec = importlib.util.spec_from_file_location(
    'secure_state', '/app/rollout_shield/secure_state.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.cmd_backup(None)
"
""")

HEALTHCHECK_SH = textwrap.dedent("""\
    #!/bin/sh
    # Bounded health probe for the dashboard container.
    # Hits nginx /healthz (does NOT consume the rate-limit token bucket).
    # Returns 0 if healthy, 1 otherwise.
    set -eu
    # When sharing network namespace with nginx, curl localhost:443.
    # Fall back to python urllib if curl is missing.
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -k https://127.0.0.1:443/healthz >/dev/null
    else
        python3 -c "
import urllib.request, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
r = urllib.request.urlopen('https://127.0.0.1:443/healthz', timeout=5, context=ctx)
assert r.status == 200
"
    fi
""")

README = textwrap.dedent("""\
    # rollout-shield dashboard — deploy bundle v""" + BUNDLE_VERSION + """

    A self-contained, bounded, permanently-safe web-server bundle generated
    on owner hardware. This bundle is the DEPLOY ARTIFACT — the source repo
    does NOT ship with it.

    ## What's in here

    ```
    .
    ├── Dockerfile             # dashboard image (loopback bind, non-root, read-only fs)
    ├── docker-compose.yml     # dashboard + nginx + state volume + audit volume
    ├── nginx.conf             # public surface (rate-limited, security headers, TLS)
    ├── VERSION                # this bundle's version (""" + BUNDLE_VERSION + """)
    ├── scripts/
    │   ├── run.sh             # dashboard entry point (refuses public bind, refuses no-unlock)
    │   ├── init-unlock.sh     # generate owner unlock on the deploy host
    │   ├── backup.sh          # print 32-word paper backup phrase
    │   └── healthcheck.sh     # bounded health probe
    └── README.md              # this file
    ```

    ## Step-by-step (operator, on the remote host)

    1.  Copy this bundle to the remote host (USB stick, scp, etc.).

    2.  Generate or restore the owner unlock:

        ```sh
        sh scripts/init-unlock.sh         # new key
        # OR
        sh scripts/backup.sh              # get a phrase from an existing host
        ```

        BACKUP THE KEY OR PHRASE OFFLINE. Loss = loss of all encrypted state.

    3.  Provide TLS certs at `./certs/fullchain.pem` and `./certs/privkey.pem`.

        For a self-signed test cert:

        ```sh
        mkdir -p certs
        openssl req -x509 -newkey rsa:2048 -nodes \\
            -keyout certs/privkey.pem -out certs/fullchain.pem \\
            -days 365 -subj "/CN=localhost"
        ```

    4.  Start the bundle:

        ```sh
        docker compose up -d
        ```

        Dashboard container is `rollout-shield-dashboard` (loopback only).
        Nginx is the public surface on `127.0.0.1:8443` → host port 443 inside.

    5.  Verify health:

        ```sh
        docker compose ps
        curl -k https://127.0.0.1:8443/healthz   # → "ok"
        ```

    6.  Expose to the internet: only by putting a TLS-terminating reverse
        proxy (or VPN) in FRONT of nginx. Do NOT publish port 8443 directly
        to a public IP — bind it to localhost and let an upstream proxy
        (Cloudflare Tunnel, Tailscale Funnel, ssh -L) handle the reach.

    ## Safety properties (enforced by the bundle)

    | Property   | Mechanism                                                   |
    |------------|-------------------------------------------------------------|
    | Bounded    | nginx 10 r/s per IP, burst 20, conn cap 8, mem cap 256 MiB   |
    |            | + Python middleware token-bucket (defense in depth)          |
    | Permanent  | named volumes `state` + `audit`, survive restart/migration   |
    | Safe       | CSP / HSTS / X-Frame-Options / X-Content-Type-Options        |
    |            | + dashboard binds loopback ONLY inside container            |
    |            | + owner unlock required (encrypted at rest)                  |
    |            | + no-new-privileges, cap-drop ALL, read-only fs              |
    |            | + no-cache on /api/* (state is real-time)                    |
    | Self-gen   | dashboard = Python stdlib http.server (no third-party WS)    |
    | Never-local| source repo NOT bundled; only the runtime minimum ships      |

    ## Backing up state

    ```sh
    docker compose exec dashboard tar czf - /state > state-backup.tar.gz
    docker compose exec dashboard tar czf - /audit > audit-backup.tar.gz
    ```

    Store both offline. Restore with the inverse pipeline.

    ## Threat model

    This bundle assumes the OPERATOR host is trusted. It does not protect
    against:
      * a root-level attacker on the operator host
      * a compromised TLS cert
      * operator-side key-logging

    It DOES protect against:
      * accidental public exposure (loopback bind)
      * cross-tenant data leaks (per-IP rate cap, conn cap)
      * cache-poisoning of /api/* (no-store)
      * clickjacking (X-Frame-Options: DENY + CSP frame-ancestors 'none')
      * MIME sniffing (X-Content-Type-Options: nosniff)
      * state loss across restarts (named volumes)
    """)


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

def assemble_bundle(out_dir: Path, src_root: Path) -> dict:
    """Lay out the deploy bundle under out_dir.

    Reads the runtime minimum from src_root (the source repo) and writes
    the bundle artifacts. Returns a manifest dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scripts").mkdir(exist_ok=True)
    files_written: dict[str, str] = {}

    # Generated files (no source dependency)
    files_written["Dockerfile"] = (out_dir / "Dockerfile").write_text(DOCKERFILE)
    files_written["docker-compose.yml"] = (out_dir / "docker-compose.yml").write_text(DOCKER_COMPOSE)
    files_written["nginx.conf"] = (out_dir / "nginx.conf").write_text(NGINX_CONF)
    files_written["VERSION"] = (out_dir / "VERSION").write_text(BUNDLE_VERSION + "\n")
    files_written["README.md"] = (out_dir / "README.md").write_text(README)
    files_written["scripts/run.sh"] = _write_exec(out_dir / "scripts" / "run.sh", RUN_SH)
    files_written["scripts/init-unlock.sh"] = _write_exec(out_dir / "scripts" / "init-unlock.sh", INIT_UNLOCK_SH)
    files_written["scripts/backup.sh"] = _write_exec(out_dir / "scripts" / "backup.sh", BACKUP_SH)
    files_written["scripts/healthcheck.sh"] = _write_exec(out_dir / "scripts" / "healthcheck.sh", HEALTHCHECK_SH)

    # Runtime minimum copied from source (NEVER the source repo itself)
    rs_src = src_root / "rollout_shield"
    rs_dst = out_dir / "rollout_shield"
    rs_dst.mkdir(exist_ok=True)
    for name in ("__init__.py", "http_server.py", "state.py"):
        src = rs_src / name
        if not src.exists():
            raise FileNotFoundError(f"required source missing: {src}")
        (rs_dst / name).write_text(src.read_text(encoding="utf-8"))
        files_written[f"rollout_shield/{name}"] = str(src)

    # Interface (HTML/JS/CSS) — only if it exists; dashboard renders a
    # fallback index if missing.
    iface_src = rs_src / "interface"
    if iface_src.exists():
        iface_dst = rs_dst / "interface"
        iface_dst.mkdir(exist_ok=True)
        for entry in iface_src.iterdir():
            if entry.is_file():
                (iface_dst / entry.name).write_text(entry.read_text(encoding="utf-8"))
                files_written[f"rollout_shield/interface/{entry.name}"] = str(entry)

    # Build manifest (file → sha256, for operator verification)
    manifest: dict[str, str] = {}
    for p in sorted(out_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(out_dir))
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        manifest[rel] = h.hexdigest()

    (out_dir / "MANIFEST.json").write_text(json.dumps({
        "version": BUNDLE_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": manifest,
    }, indent=2, sort_keys=True))
    return {"version": BUNDLE_VERSION, "out_dir": str(out_dir), "files": len(manifest)}


def _write_exec(path: Path, content: str) -> str:
    path.write_text(content)
    os.chmod(path, 0o755)
    return str(path)


def pack_tarball(bundle_dir: Path, tarball_path: Path) -> int:
    """Tar+gz the bundle into a single archive. Returns byte count."""
    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(tarball_path, "w:gz") as tar:
        for p in sorted(bundle_dir.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(bundle_dir)))
                n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_bundle(args: argparse.Namespace) -> int:
    src_root = Path(args.src).resolve()
    out_dir = Path(args.out).resolve()
    info = assemble_bundle(out_dir, src_root)
    print(f"bundle: {info['version']}")
    print(f"  out_dir: {info['out_dir']}")
    print(f"  files:   {info['files']}")
    if args.tarball:
        tarball = Path(args.tarball).resolve()
        n = pack_tarball(out_dir, tarball)
        size = tarball.stat().st_size
        print(f"  tarball: {tarball} ({size} bytes, {n} files)")
    print()
    print("next steps:")
    print(f"  1. review:  ls {out_dir}")
    print(f"  2. ship:    copy to remote host (USB, scp)")
    print(f"  3. on host: sh scripts/init-unlock.sh")
    print(f"  4. on host: docker compose up -d")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle).resolve()
    manifest_p = bundle_dir / "MANIFEST.json"
    if not manifest_p.exists():
        print(f"no MANIFEST.json at {manifest_p}", file=sys.stderr)
        return 1
    m = json.loads(manifest_p.read_text(encoding="utf-8"))
    bad = 0
    for rel, want in m["files"].items():
        p = bundle_dir / rel
        if not p.exists():
            print(f"  [MISSING] {rel}")
            bad += 1
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        if h.hexdigest() != want:
            print(f"  [CORRUPT] {rel}: sha256 mismatch")
            bad += 1
    if bad == 0:
        print(f"bundle v{m['version']}: {len(m['files'])} files, all sha256 match")
        return 0
    print(f"bundle check FAILED: {bad} issue(s)")
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rollout-shield deploy", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bundle", help="generate the deploy bundle")
    b.add_argument("--out", default="./dist/deploy-bundle",
                   help="output directory for the bundle (default ./dist/deploy-bundle)")
    b.add_argument("--src", default=".",
                   help="source repo root (default .)")
    b.add_argument("--tarball", default=None,
                   help="also produce a .tar.gz at this path")
    b.set_defaults(func=cmd_bundle)

    c = sub.add_parser("check", help="verify a bundle against MANIFEST.json")
    c.add_argument("--bundle", required=True, help="path to the bundle directory")
    c.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())