# Canary Rollout Pattern

> **Pattern**: Gradual traffic shift from the old version to the new,
> with health-signal-based promotion or rollback.
> **Status**: Pattern specification v0.1
> **Audience**: SRE / platform engineers designing rollout pipelines
> with rollout-shield claims.

## What is canary?

In a canary rollout, the new version is exposed to a small percentage
of production traffic first ("canary"), monitored for an observation
window, and either promoted to wider exposure or rolled back based on
the health signals. The classic canary progression is:

```
0% → 5% → 25% → 50% → 100%
```

Each step has its own observation window and rollback decision.
The progression is interrupted on health-signal degradation at any
step.

## Why canary?

- **Risk-bounded exposure**: a bug that manifests in 1% of traffic
  is dramatically less severe than one that manifests in 100%.
- **Real-traffic validation**: the canary sees actual production
  load, including edge cases the test suite missed.
- **Easy rollback**: route traffic back to the old version; no
  deploy needed.

## State machine

```
              ┌─────────────┐
              │  idle       │
              │  (no        │
              │   rollout)  │
              └──────┬──────┘
                     │ rollout starts
                     ▼
              ┌─────────────┐
              │ 5% canary   │──── health degraded ───→ rollback
              └──────┬──────┘                                  │
                     │ health OK                                │
                     ▼                                          ▼
              ┌─────────────┐                          ┌──────────────┐
              │ 25% canary  │──── health degraded ──→ │ rolling back │
              └──────┬──────┘                          └──────┬───────┘
                     │ health OK                                │
                     ▼                                          │ health restored
              ┌─────────────┐                                  │ (no-op for canary)
              │ 50% canary  │──── health degraded ──→ rollback  │
              └──────┬────���─┘                                  │
                     │ health OK                                ▼
                     ▼                                  ┌──────────────┐
              ┌─────────────┐                          │ rolled back  │
              │ 100%        │                          │ (back to 0%) │
              └──────┬──────┘                          └──────────────┘
                     │
                     ▼
              ┌─────────────┐
              │ completed   │
              └─────────────┘
```

Each transition emits a `change` claim (or `contradict` on rollback)
signed by the rollout-shield agent key.

## Health signals

The canary decision is driven by a small set of health signals:

| Signal | Source | Threshold (example) |
|---|---|---|
| Error rate | HTTP 5xx ratio | < 0.5% over observation window |
| p99 latency | Request duration histogram | < 1.2× of baseline |
| Saturation | CPU, memory, network | < 80% of capacity |
| Custom SLI | Application-defined | depends |

The thresholds are stored in the rollout configuration and referenced
by `change` claims. The reference implementation stores them in
`rollout/config/<env>.yaml` (gitignored; deployment-specific).

## Observation windows

| Step | Window length (default) | Configurable |
|---|---|---|
| 5% | 5 minutes | yes (per-environment) |
| 25% | 10 minutes | yes |
| 50% | 15 minutes | yes |
| 100% | 30 minutes | yes (then "completed") |

Long windows cost resources but increase confidence. Short windows
save resources but risk missing slow-burn bugs.

## claim graph for a successful canary

```
intent  (release captain → "ship v2.4.1")
  └─→ change  (CI runner → "applied v2.4.1 to canary, 5% traffic")
        └─→ test  (canary monitor → "5min p99=142ms; error rate=0.1%; pass")
              ├─→ change  (CI runner → "promoted canary 5% → 25%")
              │     └─→ test  (canary monitor → "10min p99=140ms; pass")
              │           ├─→ change  (CI runner → "promoted 25% → 50%")
              │           │     └─→ test  → ... → verify (release captain → "promote")
              │           │           └─→ change  → "promoted 50% → 100%"
              │           │                 └─→ test  → ... → verify → completed
              │           └─→ (other branch: rollback)
              └─→ (other branch: rollback at 5%)
```

## claim graph for a failed canary

```
intent  (release captain → "ship v2.4.1")
  └─→ change  (CI runner → "applied v2.4.1 to canary, 5% traffic")
        └─→ test  (canary monitor → "5min p99=420ms; FAIL exceeds threshold 1.2x")
              └─→ contradict  (rollback bot → "rolling back; threshold exceeded")
                    └─→ change  (CI runner → "rolled back; traffic restored to v2.4.0")
                          └─→ test  (post-rollback monitor → "p99=139ms; restored to baseline")
                                └─→ verify  (release captain → "rolled back; investigate v2.4.1 issue")
```

## Agent reputation impact

- A successful canary rollout: small positive reputation delta for
  the rollout agent.
- A failed canary rollout that auto-rolled-back successfully: zero
  or slightly negative delta (the agent caught the issue).
- A failed canary rollout that did NOT auto-rollback (required
  manual intervention): large negative delta.
- A failed canary rollout that did NOT rollback AND caused a
  production outage: largest negative delta; reputation enters
  `untrusted` state.

Reputation updates are computed by `tools/compute-reputation.sh`
(forthcoming) based on the claim graph.

## When NOT to use canary

- **Database schema migrations**. Stateful schema changes break
  cross-version compatibility. Use blue-green with maintenance
  window instead.
- **Long-running sessions**. Sessions pinned to a version can't
  drain smoothly across canary stages. Use blue-green instead.
- **Zero-traffic environments** (dev, staging). Just deploy.

For these cases, see `blue-green-pattern.md`.
