# Component Inventory

This is the authoritative list of every component in the rollout-shield
software layer. Each component has a primary maintainer (whoever last
modified it) and a stability classification.

## Stability classifications

- **Stable**: in production use; breaking changes require a major
  version bump and a migration guide.
- **Provisional**: in active development; breaking changes are
  expected within the next minor version.
- **Deprecated**: superseded by another component; will be removed
  in the next major version.

## Components

### Workflow components

| Path | Status | Purpose |
|---|---|---|
| `.github/workflows/docs-integrity.yml` | Stable | Verifies the versioned Beads integration block in `CLAUDE.md` is intact |
| `.github/workflows/issue-triage.yml` | Stable | Auto-labels new issues; posts welcome comment |
| `.github/workflows/pr-validate.yml` | Stable | Runs bd checks + devcontainer schema validation |
| `.github/workflows/beads-health-report.yml` | Stable | Daily read-only health check (no sync) |
| `.github/ISSUE_TEMPLATE/task.yml` | Stable | Structured intake form for agent tasks |
| `.github/dependabot.yml` | Stable | Weekly auto-update for github-actions versions |

### Composition components

| Path | Status | Purpose |
|---|---|---|
| `.github/actions/setup-bd/action.yml` | Stable | Installs bd (beads) CLI in CI; reused by multiple workflows |

### Scripts components

| Path | Status | Purpose |
|---|---|---|
| `scripts/export-state.sh` | Stable | Bundle full repo state (git + bd export) for transport |
| `scripts/setup-fork.sh` | Stable | Idempotently point origin at a personal fork |

### Tools components

| Path | Status | Purpose |
|---|---|---|
| `tools/build-card.sh` | Stable | Generate Ed25519 keypair; update `agent/identity.json` |
| `tools/verify-claim.sh` | Stable | End-to-end claim verification (schema + signature + parents + Beads) |

### Protocol components

| Path | Status | Purpose |
|---|---|---|
| `protocol/README.md` | Stable | Protocol overview; claim types, storage, verification |
| `protocol/CLAIMS.md` | Stable | Canonical claim format reference (wire format) |
| `protocol/REPUTATION.md` | Stable | Reputation scoring formula and portability rules |
| `protocol/schemas/claim.schema.json` | Stable | JSON Schema for claim validation |
| `protocol/agent-card.schema.json` | Stable | JSON Schema for agent capability cards |

### Identity components

| Path | Status | Purpose |
|---|---|---|
| `agent/identity.json` | Stable | Agent capability card (placeholder Ed25519 public key) |
| `agent/README.md` | Stable | Provisioning workflow and binding model |

### Documentation components

| Path | Status | Purpose |
|---|---|---|
| `README.md` | Stable | Top-level product positioning |
| `BRAND.md` | Stable | Brand voice, naming conventions, co-branding rules |
| `COPYRIGHT.md` | Stable | Authorship and scope statement |
| `LICENSE` | Stable | Proprietary license terms |
| `CLAUDE.md` | Stable | AI-agent instructions; Beads integration block |
| `CODEOWNERS` | Stable | Default reviewer routing |

### Analysis components

| Path | Status | Purpose |
|---|---|---|
| `docs/competitive-analysis.md` | Stable | 2026 AI provider landscape mapping + critical reading |
| `software/README.md` | Stable | Architecture overview and layer responsibilities |
| `software/components.md` | Stable | This file |

### Persistence components (runtime)

| Path | Status | Purpose |
|---|---|---|
| `.beads/config.yaml` | Stable | Beads configuration; sync.remote disabled in standalone mode |
| `.beads/metadata.json` | Stable | Beads DB metadata (project, dolt database) |
| `.beads/issues.jsonl` | Stable | Passive JSONL export of issue state |
| `.beads/interactions.jsonl` | Stable | Audit log of bd operations |
| `.beads/claims/<agent_id>/*.jsonl` | Provisional | Append-only claim DAG per agent (not yet populated) |

## Components explicitly NOT shipped

The following are referenced in spec docs but not yet implemented:

| Component | Reference | Status |
|---|---|---|
| Sample signed claim | `protocol/README.md` § Worked example | TODO |
| Worked reputation computation | `protocol/REPUTATION.md` § Score formula | TODO |
| Reference verifier in Go | `tools/verify-claim.sh` | TODO (currently bash + python) |
| Reference card builder in Go | `tools/build-card.sh` | TODO (currently bash + openssl) |
| Web UI for claim inspection | n/a | Out of scope (local-first) |
| Cloud claim relay | `protocol/README.md` § Storage | Out of scope (local-first) |
| Post-quantum hybrid signature | `hardware/README.md` § Threat model | Roadmap (v0.2) |

## Maintenance principles

1. **Additive changes only within a minor version.** New claim types,
   new optional fields, new workflow triggers — all OK without a
   major version bump.
2. **Breaking changes require a migration guide.** Schema changes
   that remove required fields, workflow files that change their
   `on:` triggers in a way that suppresses prior runs, scripts whose
   CLI surface changes incompatibly — all require a `MIGRATION.md`.
3. **Spec docs are normative; code is illustrative.** The JSON
   Schemas are the source of truth for the wire format; the bash
   tools are reference implementations, not the only valid form.
4. **No silent upgrades.** Every dependency version pin in
   `.github/dependabot.yml` requires an explicit Dependabot PR that
   the maintainer reviews and merges.
