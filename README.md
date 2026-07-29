# rollout-shield

[![version](https://img.shields.io/badge/version-0.1.0-blue.svg)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.11%2B-green.svg)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](.github/workflows/tests.yml)
[![security](https://img.shields.io/badge/security-policy-blueviolet.svg)](.github/SECURITY.md)
[![bd](https://img.shields.io/badge/issue%20tracker-bd-orange.svg)](https://github.com/gastownhall/beads)

> **repo-safe-rollouts** — verifiable code promotion from merge to production,
> with cryptographic attribution at every step.

`rollout-shield` is a hardware+software platform for **safe code rollouts**.
It combines:

- A **hardware layer** (`hardware/`) — TPM 2.0 key sealing, HSM integration via
  PKCS#11, edge-rollout testbed — that anchors signing identities to physical
  devices.
- A **software layer** (`software/`) — a Claims Protocol, signed DAG of every
  rollout step, reputation model, and tools for portable state export.
- A **rollout-pattern library** (`rollout/`) — safe-rollout patterns (canary,
  blue-green, rollback) with claim-graph integration so every promotion and
  rollback is auditable end-to-end.

See [`BRAND.md`](BRAND.md) for the full brand positioning, voice, and the
reasoning behind the Hardware+Software framing.

---

## ⚡ quick start

```bash
# 1. install the runtime into ~/usr/
git clone <repo>
cd codeam-codespace
bash scripts/install.sh

# 2. initialize state at ~/.rollout-shield/
~/usr/bin/rollout-shield install

# 3. inspect the smart-routing binding (government-version)
~/usr/bin/rollout-shield routing

# 4. run a one-shot smoke test
~/usr/bin/rollout-shield self-test

# 5. start the dashboard
~/usr/bin/rollout-shield dashboard --port 8765
# open http://127.0.0.1:8765/
```

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for production deployment
and [`docs/SECURITY.md`](docs/SECURITY.md) for the threat model.

---

## Repository layout

| Directory | Purpose |
|---|---|
| `BRAND.md` | Product brand: name, tagline, voice, positioning, H+S framing |
| `hardware/` | Hardware-layer specs (TPM, HSM, edge testbed) |
| `software/` | Software-layer specs (Claims Protocol, reputation, components) |
| `rollout/` | Safe-rollout patterns (canary, blue-green, rollback, claim-deploy) |
| `protocol/` | Claims Protocol spec (claim types, signing, DAG) |
| `agent/` | Agent specification (identity, roles, signing lifecycle) |
| `rollout_shield/` | Runtime layer: Python CLI + monitor daemon + HTTP dashboard + JSON API |
| `bin/` | Executable entry points (`rollout-shield`) |
| `tools/` | Utility scripts (keygen, claim sign/verify, rep compute, export) |
| `monitoring/` | Runtime-side monitoring reference (state layout, alert dispatch) |
| `scripts/` | Operational scripts (bd export, dolt bundle) |
| `docs/` | Public engineering documentation |
| `.github/` | GitHub Actions workflows and templates |
| `.devcontainer/` | Devcontainer template — still used to build the project workspace |
| `.beads/` | Local-first issue tracking and persistent agent memory |

---

## What is a "repo-safe-rollout"?

A safe rollout is one where:

1. **The change is known** — exact diff, commit SHA, and artifact hash are
   recorded in a signed `change` claim.
2. **The change is tested** — pre-production runs are captured in signed
   `test` claims.
3. **The change is staged** — production traffic is shifted gradually (canary
   or blue-green), never flipped in a single deploy.
4. **The change is reversible** — a rollback can be triggered automatically on
   health-signal degradation, and the rollback itself is captured as a signed
   `contradict` claim.
5. **The change is attributable** — every step is signed by a specific agent
   identity (human or AI) with cryptographic provenance.

`rollout-shield` does not implement deployment. It produces the **verifiable
evidence** that a rollout followed the patterns in `rollout/`.

---

## Status

- **Brand layer:** complete (BRAND.md + hardware/ + software/ + rollout/)
- **Claims Protocol:** complete (`protocol/CLAIM-FORMAT.md`,
  `protocol/REPUTATION.md`, `protocol/README.md`)
- **Agent spec:** complete (`agent/README.md`, `agent/identity-binding.md`,
  `agent/role-lifecycle.md`)
- **Tools:** complete (`tools/` — keygen, sign, verify, rep, export, dolt-bundle)
- **Runtime CLI + dashboard + persistent monitor:** complete
  (`rollout_shield/` Python package, `bin/rollout-shield` entry,
  `setup.sh` installer, `monitoring/` ops docs). 9 subcommands,
  Ed25519-signed claims, JSON HTTP API + vanilla-JS dashboard,
  daemon-mode monitor with webhook alerts.
- **Webhook delivery subsystem:** complete
  (`rollout_shield/webhook_delivery/` package — models, outbox,
  signer (HMAC + Ed25519), dispatcher with retry/DLQ, dedupe,
  per-target circuit breaker, replay). `rollout-shield webhooks ...`
  subcommand tree. `/api/webhooks/*` HTTP API + dashboard `Webhooks`
  tab. Atomic state at `<state_root>/webhooks/`, advisory fcntl
  lock, 44 tests. See [`docs/WEBHOOKS.md`](docs/WEBHOOKS.md).
- **Responsive dashboard** — breakpoints at 1024 / 768 / 480 px;
  tables scroll horizontally on mobile, nav tabs wrap, AI prompt
  row stacks. Both `index.html` and `ai-assistance.html` updated.
- **GitHub Actions:** complete (`.github/workflows/` — docs-integrity, triage,
  pr-validate, beads-health-report, monitor-ci; composite action `.github/actions/setup-bd`)
- **Devcontainer:** maintained (`.devcontainer/devcontainer.json` +
  `Dockerfile` — still the recommended way to open this repo)

## Quick start (runtime)

```bash
./setup.sh                                    # install + initialize state
./bin/rollout-shield status                   # system summary
./bin/rollout-shield self-check               # environment diagnostics
./bin/rollout-shield claim create \
    --agent-id my-agent \
    --type change \
    --body "applied diff abc123 to canary"    # emit a signed claim
./bin/rollout-shield monitor --once           # one-shot health report
./bin/rollout-shield monitor --daemon         # persistent monitoring
./bin/rollout-shield dashboard --port 8765    # web UI at http://127.0.0.1:8765/
```

---

## Provenance & license

Internal infrastructure — implementation details live in the private
engineering docs, not here.

See [`LICENSE`](LICENSE) and [`COPYRIGHT.md`](COPYRIGHT.md).