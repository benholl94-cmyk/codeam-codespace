# rollout-shield CLI

The runtime layer of rollout-shield: a Python 3 stdlib-only package
plus an HTTP dashboard. Composes with the spec docs in `protocol/`,
`agent/`, `rollout/`, `hardware/`, and the existing scripts in
`tools/`.

## What it does

| Subsystem | Purpose |
|---|---|
| `state.py` | Persistent on-disk state (claims, reputation, alerts, health, keys, config) |
| `cli.py` | argparse-based CLI with 9 top-level subcommands |
| `monitor_daemon.py` | Long-running process that runs health checks on an interval |
| `health_checks.py` | Pluggable health checks (state writable, disk space, claim rate, alert rate, keys, loopback) |
| `alerter.py` | Alert dispatch (persistent log + stderr + optional webhook) |
| `http_server.py` | HTTP server for the web dashboard |
| `interface/` | Static HTML + JS + CSS dashboard, served by the HTTP server |
| `commands/` | Per-subcommand implementations (keys, claim, verify, reputation, self-check) |

## Quick start

```bash
# 1. install
./setup.sh                       # or: python -m rollout_shield install

# 2. confirm the install
rollout-shield status

# 3. run a one-shot monitoring cycle
rollout-shield monitor --once

# 4. start the dashboard
rollout-shield dashboard --port 8765
# open http://127.0.0.1:8765/

# 5. start the persistent monitor daemon
rollout-shield monitor --daemon --interval 60

# 6. create a claim
rollout-shield keys new --agent-id my-agent       # one-time
rollout-shield claim create \
  --agent-id my-agent \
  --type change \
  --body "applied diff abc123 to canary"

# 7. verify the claim
rollout-shield claim list
rollout-shield verify clm_xxxxxxxxxxxxxxxx
```

## Subcommands

```
rollout-shield install                       Initialize state + default key
rollout-shield status                        System summary
rollout-shield self-check                    Diagnose environment
rollout-shield keys list                     List registered keys
rollout-shield keys new --agent-id ID        Generate a new keypair
rollout-shield keys show KEY_ID              Show key metadata
rollout-shield claim list                    List recent claims
rollout-shield claim create --agent-id ID --type TYPE --body "..."
                                             Create and sign a claim
rollout-shield claim show CLAIM_ID           Show one claim
rollout-shield verify CLAIM_ID               Verify a claim's signature
rollout-shield monitor --once                One health-check cycle
rollout-shield monitor --daemon --interval 60
                                             Long-running daemon
rollout-shield dashboard --port 8765         Serve web dashboard
rollout-shield reputation                    Reputation leaderboard
```

## State directory

By default the state lives under `./.rollout-shield/`. Override with
`--state-root DIR` or the `ROLLOUT_SHIELD_STATE` environment variable.

Layout:

```
.rollout-shield/
├── config.json                              Runtime config
├── reputation.json                          Agent → reputation index
├── daemon.json                              Daemon heartbeat
├── claims/<agent_id>/<YYYY-MM>.jsonl        Append-only claim log
├── alerts/<YYYY-MM-DD>.jsonl                Append-only alert log
├── health/<YYYY-MM-DD>.jsonl                Append-only health-check log
├── keys/<key_id>.json                       Key metadata
└── keys_material/<key_id>.pem               Private key material (chmod 0600)
```

## Dependencies

- **Python 3.8+** for the stdlib features used (walrus operator, f-strings)
- **`cryptography`** (PyPI) for Ed25519 key generation and signing/verification
  — install with `pip install cryptography` (the `./setup.sh` installer does this)

No other external runtime dependencies. The HTTP server uses `http.server`
from the stdlib. The dashboard uses vanilla HTML/JS/CSS — no npm, no bundler.

## What this CLI does NOT do

- **Deploy code**. `rollout-shield` is the runtime that **observes** rollouts
  and produces verifiable evidence. The actual deployment is the user's
  CI/CD pipeline (see `rollout/claim-deploy-pipeline.md`).
- **TPM/HSM integration**. Soft keys are generated for local development.
  In production, signing identities should be anchored in TPM 2.0 / HSM
  (see `hardware/tpm-key-storage.md` and `hardware/hsm-integration.md`).
  The CLI loads keys by metadata; a future TPM-backed loader can drop in.
- **Distributed coordination**. Single-process daemon. For multi-host
  deployments, run one daemon per host and aggregate via the HTTP API.
