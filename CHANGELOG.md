# Changelog


## v0.1.1 — 2026-07-30

Deploy bundle v0.1.1: self-contained bounded safe web server. rollout-shield deploy bundle/check subcommands; Dockerfile + nginx.conf + docker-compose.yml; per-IP token-bucket rate limiter; CSP/HSTS/X-Frame-Options/X-Content-Type-Options/Referrer-Policy; bounded (10 r/s, conn cap 8, mem cap 256 MiB); permanent (named volumes); never-localebly (source repo not bundled).

## Highlights
- **rollout-shield deploy bundle** — generates a self-contained Dockerfile + docker-compose.yml + nginx.conf + scripts bundle for shipping to a remote host
- **Bounded** — nginx 10r/s burst 20 per-IP, conn cap 8; Python token-bucket middleware (defense in depth); container pids_limit 64, cpus 0.50, mem 256 MiB
- **Permanent** — named volumes state + audit; survive restart/migration
- **Safe** — CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy / Permissions-Policy; dashboard binds 127.0.0.1:8765 inside container; nginx is the only public surface; owner unlock required; no-new-privileges; cap-drop ALL; read-only fs; no-cache on /api/*
- **Self-generated** — dashboard = Python stdlib http.server; nginx is the only non-stdlib dep
- **Never-localebly** — source repo NEVER bundled; only the runtime minimum ships
- **rollout-shield deploy check** — verifies bundle integrity against MANIFEST.json
- **12 new tests** — Total 42/42 pass
- **docs/deploy.md** — operator handbook

## Bug fixes
- **tools/leak_check.sh** — ignore nginx proxy_pass http:// and return 30x https:// directives
- **release.py** — fixed --root placement in v0.1.0

All notable changes to rollout-shield are documented in this file.
Format follows https://keepachangelog.com (kept simple).

## v0.1.0 — 2026-07-30

Zero-leak runtime + autonomy bundle: Fernet-encrypted state, hash-chained audit, 32-word paper backup, dashboard bind hardening, pre-commit leak scan, doctor + safeup + release tooling.

## Highlights
- **HTTP dashboard bind hardening** — refuses 0.0.0.0/:: without explicit --i-know-bind-is-public
- **Zero-leak at rest** — Fernet encryption of state.json, reputation.json, claim signing blocks; owner unlock file at .audit/.owner_unlock (mode 0600)
- **Hash-chained audit log** — tamper-evident .audit/audit.jsonl (SHA-256 chain), owner-only viewer
- **32-word paper backup** — BIP-39-style recovery phrase for the owner unlock
- **Audit heartbeat** — daily 'I'm alive' entry (rate-limited 1/24h); privacy audit flags if silent >25h
- **Audit rotation** — archive/ directory, bounded growth
- **Pre-commit leak_check** — git hook scans for hardcoded secrets, telemetry patterns, third-party imports, base64 blobs, sensitive file tracking
- **tools/doctor.py** — 10 health checks across python/crypto/state/keys/logs/safeups/git/beads
- **tools/safeup.py** — rotating snapshot+rollback with checksum verification, recursive preop auto-restore on failure
- **tools/release.py** — snapshot → tests → version bump → CHANGELOG → tag (this release)
- **tools/secure_state.py** — Fernet wrapper with --init / --status / --backup / --recover / --verify-phrase
- **State migration framework** — @register_migration decorator + migrate() chain in rollout_shield/state.py
- **Autonomie skill bundle** — SKILL.md + 4-script chain loaded automatically by owner-repo input

