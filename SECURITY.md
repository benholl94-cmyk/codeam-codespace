# Security & Zero-Leak Architecture

This document describes the threat model, the protections in place, and how
the owner can verify the protections are intact.

## Threat Model

| Adversary | Capability | Defended by |
|---|---|---|
| Cloud backup scanner | Reads files in synced directories | Fernet encryption at rest (`.audit/.owner_unlock` gates reads) |
| Accidental `git add` of state | Developer commits `.rollout-shield/` or `.audit/` | `.gitignore` hardening + `tools/leak_check.sh` |
| Third-party file indexer | Scans repo for telemetry/analytics | No telemetry; `leak_check.sh [5]` enforces |
| Workflow exfiltration | CI runs `curl -d $SECRET` | `leak_check.sh [8]` flags POST-with-body patterns |
| Repo scrape | Reads every committed file | No secrets, no keys, no tracking (see `leak_check.sh`) |
| Tampered audit log | Someone edits `.audit/audit.jsonl` | Hash-chained entries; `tools/owner_log.py verify` detects |
| Local file disclosure | Process reads state files outside intended flow | Mode 0600 on `.owner_unlock`; mode 0700 on `.audit/` |
| Network exfiltration | Code POSTs data to attacker | `leak_check.sh [2]` flags any third-party network lib; only stdlib `urllib`/`socket` allowed (user-configured endpoints only) |

Out of scope (the owner should know):

- GitHub itself can see what you push. If you need to keep a file off the
  GitHub server, do not push it. Use `.gitignore` + local-only storage.
- The host OS can read process memory. Memory-resident secrets are not
  protected against a determined attacker with root access.
- The container/VM hosting Claude can see inputs and outputs sent to the
  Claude API. This is a hard limit of running on cloud infrastructure.

## Architecture

```
                ┌──────────────────────────────────────────┐
                │ owner hardware (smartphone / laptop)     │
                │   reads: tools/owner_log.py {tail,verify}│
                └────────────────────┬─────────────────────┘
                                     │ human-visible audit log
                                     ▼
   ┌─────────────────────────────────────────────────────────┐
   │ .audit/                                                │
   │   ├── .owner_unlock   (mode 0600, Fernet key)          │
   │   └── audit.jsonl     (hash-chained, mode 0700)        │
   └────────────────────┬────────────────────────────────────┘
                        │ gates reads/writes
                        ▼
   ┌─────────────────────────────────────────────────────────┐
   │ encrypted state (.rollout-shield/ + sensitive JSON)     │
   │   only decryptable when owner_unlock is present         │
   └─────────────────────────────────────────────────────────┘
                        ▲
                        │ only stdlib + cryptography
                        │
   ┌─────────────────────────────────────────────────────────┐
   │ runtime code: rollout_shield/, tools/*.py              │
   │   * no third-party imports (leak_check [4])            │
   │   * no telemetry (leak_check [5])                      │
   │   * no secrets in code (leak_check [3])                │
   │   * audit append on every state-touching op            │
   └─────────────────────────────────────────────────────────┘
```

## How the owner verifies

Run the full privacy audit:

```bash
tools/leak_check.sh
tools/owner_log.py verify
tools/secure_state.py --status
```

Or in one go (after wiring):

```bash
tools/privacy_audit.sh
```

The audit emits a pass/fail per check and exits non-zero on any failure.

## How Claude is constrained

- `tools/leak_check.sh` runs as part of CI / pre-commit. Any new
  third-party import, telemetry pattern, or tracked secret fails the gate.
- `tools/audit_log.py` is imported by every state-touching tool. Each
  append is hash-chained; tampering invalidates the chain (detected by
  `owner_log.py verify`).
- `tools/secure_state.py --check` exits 1 when the owner unlock is
  absent. Sensitive operations gate on this.
- No background daemons, no scheduled tasks, no auto-updaters. Every
  action is initiated by the owner.

## How the owner grants Claude read access

The `.audit/.owner_unlock` file is the gate. When it exists:

- `tools/owner_log.py` reads the log normally (the log itself is meant
  to be visible to the owner — what it describes is encrypted separately).
- `tools/secure_state.py` decrypts sensitive state.

When it does NOT exist:

- Encrypted reads return None (no data exposure).
- Encrypted writes raise `StateLockedError`.
- The audit log can still be appended (it lives outside the encrypted zone).

The owner creates the unlock on their hardware:

```bash
python3 tools/secure_state.py --init
```

…and removes it when stepping away:

```bash
python3 tools/secure_state.py --remove  # (or just `rm` the file)
```

This is the **only** mechanism Claude uses to determine whether to operate
on sensitive state. Without the file on disk, no decryption occurs.

## Incident response

If you suspect a leak:

1. `tools/owner_log.py verify` — was the audit log tampered with?
2. `tools/leak_check.sh` — did any new code introduce telemetry / secrets?
3. `git log --all -- .audit/ .rollout-shield/` — anything accidentally
   committed? (Both paths are gitignored, so this should be empty.)
4. Rotate the unlock: `python3 tools/secure_state.py --init-force` —
   this invalidates all prior encrypted state (you'll lose it).
5. Restore from a known-good safeup: `tools/safeup.py restore <id>`.
6. Audit recent alerts: `python3 tools/owner_log.py grep fail`.
