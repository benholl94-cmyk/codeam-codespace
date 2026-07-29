# workspaces — sub-workspaces of codeam-codespace

This repository is the **repo-workspace** for the codeam/rollout-shield
system. It is composed of several **sub-workspaces**, each with a
narrow scope, its own README, and its own bd memory pool.

```
codeam-codespace/  ← repo-workspace (here)
├── rollout_shield/           core runtime package (Python 3, stdlib)
├── bin/                      CLI entry scripts
├── scripts/                  install / uninstall / verify / fork / export
├── protocol/                 spec — wire format for claims
├── agent/                    spec — agent identity + key handling
├── rollout/                  spec — canary/blue-green/rollback patterns
├── hardware/                 spec — TPM/HSM key anchoring
├── tools/                    spec — primitives + utilities
├── codegen/                  spec — code-generation primitives
├── test/                     spec — test patterns
├── dashboard/      ★ NEW     web UI (extracted from rollout_shield/interface/)
├── benchmarks/     ★ NEW     perf benchmarks sub-workspace
├── tests/          ★ NEW     pytest suite (unit + integration + smoke)
├── docs/           ★ NEW     top-level documentation
├── examples/       ★ NEW     example configs + deployment scripts
├── .github/                  CI workflows
├── .beads/                   bd issue tracker (Dolt DB)
└── .claude/                  agent context profiles
```

★ = new sub-workspace from the production-grade hardening pass.

## sub-workspace scope map

| sub-workspace | scope | talks to | owns |
|---|---|---|---|
| `rollout_shield/` | core runtime (CLI + daemon + http server + AI layer + state) | All other sub-workspaces | the publishable artifact |
| `dashboard/` | web UI assets (HTML/CSS/JS) served by the http server | `rollout_shield/http_server.py` | asset layout, dev-server instructions |
| `benchmarks/` | micro + AI performance benchmarks | `rollout_shield/ai/`, `~/.rollout-shield/` | the running-cost story |
| `tests/` | pytest suite | `rollout_shield/` (imports the package) | all assertions, fixtures, smoke |
| `docs/` | top-level documentation | every sub-workspace | the on-boarding read |
| `examples/` | example configs + scripts | `rollout-shield` (CLI) | deployment templates |
| `protocol/` | wire-format spec | `rollout_shield/commands/claim.py` | canonical claim schema |
| `agent/` | agent identity spec | `rollout_shield/commands/keys.py` | key lifecycle + hardware anchoring |
| `rollout/` | rollout-pattern spec | `rollout_shield/ai/` (fka-deploy-pipeline) | canary/blue-green/rollback |
| `hardware/` | TPM/HSM integration spec | `agent/` | hardware-anchored signing |
| `tools/` | primitive utilities | `scripts/` | shared helpers |
| `codegen/` | code-generation primitives | `rollout/` (claim-deploy-pipeline) | FKA artifact generators |
| `test/` | test-pattern spec | `tests/` | test conventions |
| `bin/` | CLI entry scripts | `rollout_shield/cli.py` | the user-facing commands |
| `scripts/` | installation + lifecycle | `rollout_shield/` | install/uninstall/verify |
| `.github/` | CI workflows | `tests/` | automated quality gates |
| `.beads/` | issue tracker (Dolt) | — | the task ledger |
| `.claude/` | agent context profiles | — | per-agent instructions |

## what each sub-workspace reads

```
                                            ┌─── rollout_shield ─┐
dashboard ──────── http_get ───────────────▶│                    │
benchmarks ─────── subprocess ─────────────▶│                    │
tests ──────────── import ────────────────▶│                    │
examples ───────── subprocess ─────────────▶│                    │
scripts ────────── subprocess ─────────────▶│                    │
docs ────────────── (static files) ───────▶│                    │
                                            └────────────────────┘
                                                       │
                                                       ▼
                                         state at ~/.rollout-shield/
                                         install at ~/usr/
```

## ordering sub-workspaces

If you are new to the repo, read the sub-workspaces in this order:

1. `docs/ARCHITECTURE.md` — the high-level picture
2. `docs/WORKSPACES.md` (this file) — what's where
3. `protocol/CLAIM-FORMAT.md` — the data model
4. `rollout_shield/` — the runtime
5. `dashboard/` — the UI
6. `benchmarks/` — the cost story
7. `tests/` — the safety net
8. `examples/` — the deployment templates
9. `hardware/` — the production-grade story
