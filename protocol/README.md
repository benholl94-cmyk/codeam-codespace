# Agent Claims Protocol

> **Version**: 0.1 (proposed)
> **Status**: Specification draft
> **Companion docs**: `REPUTATION.md`, `agent-card.schema.json`

## Purpose

Define a structured, verifiable protocol by which AI coding agents attach
**claims** to their output, so that:

1. The claim is machine-checkable (not just human-readable)
2. The claim is cryptographically signed (provable authorship)
3. The claim is addressable in a global graph (DAG-addressable)
4. The claim feeds into a reputation system (see `REPUTATION.md`)

## Background

The 2026 user-feedback landscape converges on one structural gap: AI agent
output lacks provenance. Every provider sits at the "low" end of the
provenance spectrum (see `docs/competitive-analysis.md` §1.2). The
Claims Protocol is the missing layer.

## Design principles

1. **Local-first.** All claims are produced, signed, stored, and verified
   locally. No cloud dependency. Optional relays may sync claims across
   machines but the protocol is fully usable without them.
2. **Model-agnostic.** The protocol wraps any model output. It does not
   constrain the model or its system prompt.
3. **Surface-agnostic.** Works from any agent (terminal, IDE, sandbox,
   CI pipeline).
4. **Append-only.** Claims form an append-only DAG. Edits are modeled
   as new claims referencing old ones.
5. **Cryptographically anchored.** Every claim carries an Ed25519
   signature from a long-lived agent keypair bound to the workspace's
   git committer identity.
6. **Composable with existing tooling.** Claims reference Beads issue
   IDs by default; no parallel issue tracker required.

## Claim types

The protocol defines an extensible set of claim types. Implementations
MUST support these core types; new types MAY be added via JSON Schema
extension.

| Type | Direction | What it asserts |
|---|---|---|
| `intent` | human → agent | "Issue X requires a change with these acceptance criteria" |
| `change` | agent → repo | "I produced this diff against the issue-graph state at hash H" |
| `test` | agent → repo | "I ran test suite Y; here is the result hash" |
| `verify` | human/agent → claim | "I confirm the outcome of claim C" |
| `contradict` | human/agent → claim | "I reject the outcome of claim C with reason R" |
| `delegate` | agent → agent | "I hand off responsibility for issue X to agent Y" |

Each type has a specific JSON Schema (see `protocol/schemas/`).

## Claim graph

Claims form a DAG with the following edges:

```
intent → change
change → test
test → verify
test → contradict
change → delegate (when agent hands off)
verify → change (cross-issue: change-of-issue closure depends on verify)
```

The DAG is **append-only**. To retract a claim, the protocol emits a new
`contradict` claim referencing the original; the original claim remains
in the graph with its now-contradicted status. This mirrors the
Behaviors of Git's own object model and is chosen for the same reasons:
history rewriting is more expensive than honest history-keeping.

## Storage

Claims are stored as JSON-Lines files, one claim per line:

```
.beads/claims/<agent-id>/<year>-<month>.jsonl
```

The path scheme ensures that:
- Each agent has its own namespace (no cross-agent write conflicts)
- Files are bounded in size (~ a month of activity per file)
- Claims are temporally ordered for cheap streaming
- The directory is gitignored (see `.beads/.gitignore`)

## Verification

A claim is verifiable if and only if:

1. Its JSON validates against the corresponding type schema
2. Its `signature` field is a valid Ed25519 signature over the canonical
   JSON (RFC 8785 JCS) of the claim body
3. The signing public key matches the agent identity declared in
   `agent/identity.json`
4. All referenced prior claims (by `parent_hashes`) are present in the
   graph
5. All referenced Beads issue IDs exist (if `bd` is available locally)

Reference verifier: `tools/verify-claim.sh`.

## Anti-patterns the protocol explicitly rejects

- **Implicit claims**: agents that ship output without an attached claim
  gain no reputation and cannot be trusted by other agents.
- **Self-verifying claims**: `verify` claims MUST be authored by a
  different agent identity than the claim they verify. Self-verification
  is structurally equivalent to no verification.
- **Opaque test results**: `test` claims MUST include a content hash of
  the test output (logs, exit codes, diff), not just a one-line summary.
- **Hidden delegation**: agents that perform work on behalf of others
  without a `delegate` claim cannot be held accountable for that work.

## Worked example

See `EXAMPLE.md` (forthcoming) for a full end-to-end example showing
intent → change → test → verify across two agents and a human reviewer.

## Versioning

This document is versioned semver. Breaking changes to the wire format
require a major version bump. Additive changes (new claim types,
optional fields) are minor.
