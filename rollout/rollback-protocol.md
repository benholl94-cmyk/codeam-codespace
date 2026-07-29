# Rollback Protocol

> **Pattern**: Universal — how to roll back safely regardless of which
> rollout pattern (canary, blue-green, or direct deploy) was used.
> **Status**: Pattern specification v0.1
> **Audience**: SRE / on-call engineers; rollout-shield agents.

## What is rollback?

A rollback is the act of reverting the production system to a previous
known-good version after a failed rollout. Rollback is the safety net
that makes any rollout pattern viable — without rollback, every
rollout is a one-shot gamble.

## Why a separate protocol?

Rollback is universal: it works the same way regardless of how the
forward rollout was performed. The rollback logic does not depend
on whether the forward rollout was canary, blue-green, or a single
direct deploy. Therefore, the rollback protocol lives in its own
document and is invoked from the canary and blue-green patterns
on failure.

## Three rollback modes

| Mode | Use case | Speed | Cost |
|---|---|---|---|
| **Traffic-only** (preferred) | Canary / blue-green still has old version live | Seconds (LB switch) | Zero |
| **Re-deploy** | Old version not live; need to redeploy | Minutes (full deploy) | Deploy cost |
| **DB-forward** | Old version doesn't support current schema | Hours (forward-fix migration) | Engineering cost |

Mode 1 is the default for canary and blue-green. Mode 2 applies when
a "deploy and forget" direct-deploy was used. Mode 3 is the
worst-case escape hatch.

## Mode 1: Traffic-only rollback (preferred)

```
1. Health-signal monitor emits a `contradict` claim against the
   rollout's `change` claim. The claim body explains the failure
   (e.g., "5min p99 latency 412ms exceeds 1.2x baseline").

2. Rollback bot (or human on-call) confirms the rollback. The
   confirmation is itself a `verify` claim against the `contradict`
   claim. (Self-verification is forbidden; the rollback confirmation
   must come from a different agent identity.)

3. Rollback bot issues a `change` claim: "rolled back; traffic
   shifted to old version". This claim has the old version's
   deployment as its parent.

4. The CI runner or LB controller shifts traffic back.

5. Post-rollback monitor runs for an observation window; emits
   `test` claims verifying the rollback restored baseline health.

6. On confirmation, rollback bot emits `verify` claim: "rollback
   successful; production restored to baseline".
```

The full claim graph captures:
- The failure mode (what was observed).
- The decision point (who authorized the rollback).
- The rollback action (what was executed).
- The verification (the system is healthy again).

## Mode 2: Re-deploy rollback

When the old version is no longer live (e.g., the forward rollout
was a direct deploy that replaced the old version's containers),
re-deployment is needed:

```
1. Rollback bot identifies the last known-good version (typically
   the previous Beads-closed issue's artifact hash).
2. Rollback bot issues a `change` claim: "re-deploying v_PREVIOUS
   to all production nodes".
3. CI runner executes the re-deploy.
4. Post-rollback monitor emits `test` claims verifying restored
   health.
5. On confirmation, rollback bot emits `verify` claim.
```

This mode is slower (a full deploy cycle) and more expensive
(network egress, compute time), but works in any environment.

## Mode 3: DB-forward rollback (worst case)

When the old version's code is incompatible with the current DB
schema (e.g., a forward migration dropped a column the old code
relied on), you cannot simply re-deploy the old code. You must
either:

- **Forward-fix**: write a new migration that puts the schema back
  in a state the old code can use, then re-deploy the old code.
  This is engineering work; expect hours.
- **Forward-only**: keep the new code; fix the bug that triggered
  the rollback. This may be faster than forward-fix, depending on
  the nature of the bug.

In either case, the rollback is captured as a claim chain:

```
contradict  (failure detected)
  └─→ change  (forward-fix migration applied)
        └─→ test  (DB schema verified at the forward-fix state)
              └─→ change  (old code re-deployed against fixed schema)
                    └─→ test  (production verified healthy)
                          └─→ verify  (rollback complete)
```

## Rollback decision criteria

The decision to roll back (rather than push forward) is driven by:

| Signal | Threshold (example) | Action |
|---|---|---|
| Error rate | > 0.5% over 5 min | Rollback |
| p99 latency | > 1.5x baseline over 5 min | Rollback |
| Saturation | > 90% CPU / memory for 5 min | Rollback |
| Manual alert | On-call declares incident | Rollback (after triage) |
| Self-healing | Bug appears minor; fix expected in < 30 min | Forward-fix instead |

The thresholds are configured per environment in
`rollout/config/<env>.yaml`. The same thresholds that gate forward
progression also gate against rollback.

## claim graph semantics

A rollback produces a different claim graph shape than a forward
rollout:

| Forward rollout | Rollback |
|---|---|
| Linear: intent → change → test → verify | Branching: ... test (fail) → contradict → change (rollback) → test → verify |
| All claims lead to a single `verify` that completes the rollout | The `verify` closes the contradiction, not the rollout |

The `completed` state of a rollout is reached only on a successful
forward rollout. A rolled-back rollout ends in a `rolled_back`
state; the original rollout is considered incomplete and the issue
remains open in Beads (requires a new fix to close).

## Agent reputation impact

| Scenario | Reputation delta |
|---|---|
| Successful forward rollout | small positive |
| Failed rollout + automatic rollback | zero or slightly negative (caught the bug) |
| Failed rollout + manual rollback | moderate negative (didn't catch it) |
| Failed rollout + manual rollback + production impact | large negative |
| Failed rollback (rollback itself failed) | largest negative; agent enters `untrusted` state |

The reputation model uses these deltas to compute the long-term
score; see `protocol/REPUTATION.md` for the formula.

## What this protocol does NOT cover

- **Data-layer rollbacks**. If the rollback needs to revert a
  database state (e.g., a destructive DELETE), that requires a
  separate data-recovery procedure (backup restore, point-in-time
  recovery). Out of scope.
- **Communication with users**. User-facing incident communication
  (status page updates, customer notifications) is owned by the
  communications team, not the rollout system.
- **Post-mortem**. After a rollback, a post-mortem should be
  conducted. The claim graph is the input to the post-mortem
  (the auditable record of what happened), but the post-mortem
  document itself is not part of this protocol.
