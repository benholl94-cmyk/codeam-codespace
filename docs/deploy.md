# Deploying rollout-shield — bounded, permanent, safe

This document describes how to deploy the rollout-shield dashboard to a
remote host as a self-contained, bounded, permanently-safe web-server bundle.

> **Zero-leak principle:** the source repo never leaves the owner hardware.
> The deploy bundle is the artifact you ship.

## 1. Generate the bundle

On the owner hardware (where the source repo lives):

```sh
rollout-shield deploy bundle --out ./dist/deploy-bundle \
                             --tarball ./dist/deploy-bundle.tar.gz
```

This produces:

```
dist/
├── deploy-bundle/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── nginx.conf
│   ├── VERSION                # 0.1.1
│   ├── MANIFEST.json          # sha256 of every file
│   ├── README.md
│   ├── scripts/
│   │   ├── run.sh
│   │   ├── init-unlock.sh
│   │   ├── backup.sh
│   │   └── healthcheck.sh
│   └── rollout_shield/
│       ├── __init__.py
│       ├── http_server.py
│       ├── state.py
│       └── interface/
└── deploy-bundle.tar.gz        # ~19 KB
```

Verify the bundle before shipping:

```sh
rollout-shield deploy check --bundle ./dist/deploy-bundle
```

## 2. Ship to the remote host

```sh
scp dist/deploy-bundle.tar.gz operator@host:/srv/
ssh operator@host
cd /srv && tar xzf deploy-bundle.tar.gz && cd deploy-bundle
```

## 3. Generate the owner unlock (DESTRUCTIVE if overwriting)

```sh
sh scripts/init-unlock.sh
# OR: sh scripts/init-unlock.sh --force    # overwrites existing
```

**BACKUP THE KEY OR PHRASE OFFLINE.** Loss = loss of all encrypted state.

## 4. Provide TLS certs

```sh
mkdir -p certs
# Self-signed (test only):
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout certs/privkey.pem -out certs/fullchain.pem \
    -days 365 -subj "/CN=localhost"
# OR use Let's Encrypt (certbot) for production.
```

## 5. Start

```sh
docker compose up -d
docker compose ps
curl -k https://127.0.0.1:8443/healthz   # → "ok"
```

The dashboard container binds `127.0.0.1:8765` (loopback only). nginx is
the public surface on `127.0.0.1:8443`. **Do not** publish port 8443 directly
to a public IP — expose it via a TLS-terminating reverse proxy, Cloudflare
Tunnel, Tailscale Funnel, or `ssh -L`.

## 6. Backup state

```sh
docker compose exec dashboard tar czf - /state > state-backup.tar.gz
docker compose exec dashboard tar czf - /audit > audit-backup.tar.gz
```

Restore with the inverse pipeline.

## Safety properties (enforced by the bundle)

| Property   | Mechanism                                                   |
|------------|-------------------------------------------------------------|
| **Bounded** | nginx 10 r/s per IP, burst 20, conn cap 8, mem cap 256 MiB  |
|            | + Python middleware token-bucket (defense in depth)         |
|            | + container pids_limit 64, cpus 0.50, mem 256m              |
| **Permanent** | named volumes `state` + `audit`; survive restart/migration |
| **Safe**   | CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy |
|            | + dashboard binds loopback ONLY inside container            |
|            | + owner unlock required (encrypted state at rest)           |
|            | + no-new-privileges, cap-drop ALL, read-only fs             |
|            | + no-cache on /api/* (state is real-time)                   |
| **Self-generated** | dashboard = Python stdlib `http.server` (no third-party WS) |
| **Never-localebly** | source repo NOT bundled; only the runtime minimum ships |

## Threat model

The bundle assumes the operator host is trusted. It does not protect
against a root-level attacker on the operator host, a compromised TLS
cert, or operator-side key-logging. It DOES protect against accidental
public exposure, cross-tenant leaks, cache poisoning of `/api/*`,
clickjacking, MIME sniffing, and state loss across restarts.

## Exit codes

| Code | Meaning                                |
|------|----------------------------------------|
| 0    | OK                                     |
| 1    | invalid arguments                      |
| 2    | source files missing / bundle corrupt  |
| 3    | source repo not found                  |