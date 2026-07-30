# Changelog

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

