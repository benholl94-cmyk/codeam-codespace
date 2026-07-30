# Changelog




## v0.3.0 — 2026-07-30

Pip-installable rollout-shield v0.3.0: pyproject.toml with PEP 621 metadata + 'rollout-shield' console-script entry point + OWNER-FIRST-LICENSE.md (custom zero-leak proprietary license with 7 explicit sections including Owner's rights, third-party prohibitions, architectural guarantees).

## Highlights
- **pip-installable** — 'pip install -e .' (or 'pip install .') on any Python 3.10+ host installs the 'rollout-shield' console script (entry point: rollout_shield.cli:main)
- **pyproject.toml** (PEP 621): setuptools backend, requires-python >=3.10, deps = ['cryptography>=41.0.0'] (Fernet), dev/android/deploy optional-dependency groups, py.typed marker (PEP 561)
- **OWNER-FIRST-LICENSE.md** — custom proprietary license:
  - Section 1 — Owner's rights (unrestricted)
  - Section 2 — Third-party prohibitions (no telemetry, no exfil, no deanonymization, no bypass)
  - Section 3 — Architectural guarantees (no outbound network, encrypted at rest, tamper-evident audit, loopback by default, owner-unlock gating)
  - Section 4-7 — No warranty, termination, governing law, entire agreement
- **Verified end-to-end** on this box in clean venv: pip install, --version (0.3.0), --help (10 subcommands), deploy bundle, deploy check
- **10 new tests** (pyproject metadata, OWNER-FIRST clauses, CLI import, version output, all subcommands --help). Total 52/52 pass

## What the user does on their hardware
```sh
# on Termux (Android) or any Linux box with Python 3.10+
git clone https://github.com/benholl94-cmyk/codeam-codespace
cd codeam-codespace
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                       # or 'pip install .' for non-editable
rollout-shield --version               # 'rollout-shield 0.3.0'
rollout-shield install
rollout-shield dashboard               # bound to 127.0.0.1:8765
```

## v0.2.0 — 2026-07-30

Native Android app v0.2.0: real APK (NOT PWA) with homescreen icon, loopback-only HTTP server inside the app, no INTERNET permission, owner-unlock gated. Built with Kotlin + Gradle (AndroidX only, no third-party UI deps).

## Highlights
- **Real native Android app** — produces a real APK that, when installed (adb install / sideload), shows up on the homescreen with a real adaptive launcher icon (shield+R vector monogram, no PNG required)
- **NOT a PWA** — no service worker, no manifest.webmanifest, no browser tab. Pure Kotlin (MainActivity + LocalServer + LocalCrypto) + AndroidX/Material
- **Loopback-only at the OS level**:
  * AndroidManifest declares NO <uses-permission android:name="android.permission.INTERNET" /> — app physically cannot reach the network
  * network_security_config.xml: cleartext denied globally; 127.0.0.1 + localhost explicitly allowed only
  * LocalServer.kt binds InetAddress.getByName('127.0.0.1') ONLY (never 0.0.0.0)
- **Owner-unlock gated** — refuses to start the WebView until filesDir/owner_unlock is present; if absent, shows 'Unlock required' screen with local-generation button
- **Cloud-backup disabled** — data_extraction_rules.xml blocks Google Auto Backup and device transfer across all storage domains
- **Standalone / no external service** — zero Google Play Services, Firebase, or telemetry dependencies
- **Build paths**:
  * Desktop: open android-app/ in Android Studio Hedgehog+, Build APK
  * On-device: build-on-termux.sh installs cmdline-tools + SDK 34 + gradle assembleDebug → app-debug.apk
- **No external URLs in build** — gradle-wrapper.properties deliberately omitted (uses installed gradle); the build itself works offline once AndroidX caches are warm

## Files (18 total)
- android-app/{settings,build}.gradle.kts, gradle.properties, README.md, build-on-termux.sh
- app/build.gradle.kts, app/src/main/AndroidManifest.xml
- app/src/main/kotlin/com/rolloutshield/dashboard/{MainActivity,LocalServer,LocalCrypto}.kt
- app/src/main/res/{values/{strings,colors,themes}.xml, drawable/ic_launcher_foreground.xml,
  mipmap-anydpi-v26/ic_launcher{,_round}.xml, xml/{network_security_config,data_extraction_rules}.xml}

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

