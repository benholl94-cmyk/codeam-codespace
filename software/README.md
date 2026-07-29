# Software Layer

The software layer of `rollout-shield` is everything that runs in
software: the Claims Protocol, the agent identity layer, the
reputation model, the devcontainer template, the GitHub Actions
workflows, the scripts, and the tools.

This directory documents the software architecture and component
inventory. It does not duplicate the spec docs in `protocol/`,
`agent/`, or `tools/` — those are the source of truth for their
respective layers. This directory is the *map*.

---

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         User-facing surface                       │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ GitHub UI  │  │  bd CLI      │  │  rollout-shield CLI      │  │
│  │ (issues,   │  │  (issue      │  │  (claim sign/verify,     │  │
│  │  PRs,      │  │   tracking)  │  │   card build, identity   │  │
│  │  Actions)  │  │              │  │   rotate)                │  │
│  └─────┬──────┘  └──────┬───────┘  └────────────┬─────────────┘  │
└────────┼────────────────┼─────────────────────────┼───────────────┘
         │                │                         │
         ▼                ▼                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Workflow layer (.github/)                      │
│  ┌────────────────┐  ┌─────────────────┐  ┌────────────────────┐ │
│  │ docs-integrity │  │ issue-triage    │  │ pr-validate        │ │
│  │ beads-health   │  │ dependabot      │  │                    │ │
│  └────────┬───────┘  └────────┬────────┘  └─────────┬──────────┘ │
└───────────┼────────────────────┼────────────────────┼────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                Composition layer (.github/actions/)               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  setup-bd  — single source of truth for bd installation   │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│                       Scripts layer (scripts/)                    │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐  │
│  │ export-state.sh  │  │ setup-fork.sh                        │  │
│  │  (portable       │  │  (idempotent fork-as-origin)         │  │
│  │   bundle)        │  │                                      │  │
│  └──────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Tools layer (tools/)                      │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐  │
│  │ build-card.sh    │  │ verify-claim.sh                      │  │
│  │  (Ed25519 key-   │  │  (end-to-end claim verification)     │  │
│  │   pair)          │  │                                      │  │
│  └──────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Protocol layer (protocol/)                    │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ README.md  │  │ CLAIMS.md    │  │ REPUTATION.md            │  │
│  │  (overview)│  │  (format)    │  │  (scoring)               │  │
│  └────────────┘  └──────────────┘  └──────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  schemas/claim.schema.json + agent-card.schema.json         │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│                       Identity layer (agent/)                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  identity.json — Ed25519 public key + bound committer       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│              Persistence layer (.beads/ + .beads/claims/)         │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐  │
│  │ Dolt DB          │  │ JSONL claim log                      │  │
│  │  (issue tracker) │  │  (append-only DAG)                   │  │
│  └──────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Layer responsibilities

| Layer | Owns | Does NOT own |
|---|---|---|
| User-facing surface | UX, ergonomics, docs | Implementation, storage |
| Workflow layer | Triggering, sequencing | Spec, storage |
| Composition layer | Reusable building blocks | Triggering |
| Scripts layer | Operator ergonomics | Spec, automation |
| Tools layer | Reference implementations | Production deployment |
| Protocol layer | Spec, schemas, validation | Implementation |
| Identity layer | Cryptographic identity | Authorization policy |
| Persistence layer | Storage, retrieval | Schema, validation |

Each layer depends only on the layers below it. Cross-layer
dependencies (e.g., tools directly writing to persistence) are
forbidden.

---

## Data flow

A typical rollout-shield workflow:

1. User opens an issue on GitHub → `issue-triage` workflow labels it.
2. Maintainer creates a matching Beads issue: `bd create --title=...`.
3. Agent (or maintainer) claims the issue: `bd update <id> --claim`.
4. Agent produces a `change` claim (signed with the workspace key).
5. `pr-validate` workflow runs `bd doctor`, `bd lint`, `bd orphans`,
   schema-checks `devcontainer.json`.
6. Reviewer runs `tools/verify-claim.sh <change-claim.json>`.
7. On approval, reviewer emits a `verify` claim.
8. Reputation model updates: `V(agent) += 1`, score recomputed.
9. CI runner signs a `change` claim with the rollout-shield agent
   key, attaches to the deployment, deploys to canary.
10. Testbed emits `test` claims with health signals.
11. On quorum of `test` + `verify` claims, CI promotes to
    production.

Steps 4–11 form the *core rollout pipeline*; the rest is context.

---

## Component inventory

| Path | Layer | Lines (approx) | Status |
|---|---|---|---|
| `.github/workflows/*.yml` | Workflow | 600+ | Stable |
| `.github/actions/setup-bd/action.yml` | Composition | 60 | Stable |
| `.github/ISSUE_TEMPLATE/task.yml` | Workflow | 50 | Stable |
| `scripts/export-state.sh` | Scripts | 80 | Stable |
| `scripts/setup-fork.sh` | Scripts | 80 | Stable |
| `tools/build-card.sh` | Tools | 100 | Stable |
| `tools/verify-claim.sh` | Tools | 200 | Stable |
| `protocol/README.md` | Protocol | 200 | Stable |
| `protocol/CLAIMS.md` | Protocol | 200 | Stable |
| `protocol/REPUTATION.md` | Protocol | 200 | Stable |
| `protocol/schemas/claim.schema.json` | Protocol | 200 | Stable |
| `protocol/agent-card.schema.json` | Protocol | 100 | Stable |
| `agent/identity.json` | Identity | 30 | Stable (placeholder key) |
| `.beads/` | Persistence | runtime | Active |

---

## What is NOT in this layer

- **Production deployment automation** (k8s manifests, Terraform
  modules, Ansible playbooks). Those live in `rollout/` (a sibling
  directory).
- **Hardware specifications** (TPM, HSM, edge testbeds). Those live
  in `hardware/` (a sibling directory).
- **Marketing, branding, voice/tone**. Those live in `BRAND.md`.
- **Competitive analysis**. That lives in `docs/`.
