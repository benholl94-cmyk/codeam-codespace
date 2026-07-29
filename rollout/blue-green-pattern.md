# Blue-Green Rollout Pattern

> **Pattern**: Two parallel environments (blue and green) with
> instantaneous traffic switchover via load balancer.
> **Status**: Pattern specification v0.1
> **Audience**: SRE / platform engineers designing rollout pipelines
> for stateless applications.

## What is blue-green?

In a blue-green rollout, two identical production environments exist
("blue" and "green"), but only one receives traffic at a time. To
roll out a new version:

1. Deploy the new version to the idle environment (e.g., green).
2. Run pre-switchover validation against green (smoke tests, health
   probes, canary-shaped synthetic traffic).
3. Switch the load balancer to point at green.
4. Monitor for the observation window.
5. Either keep green active (rollback = point back at blue) or
   tear down blue and rename green.

Unlike canary, blue-green is binary: 0% or 100%, no in-between.

## Why blue-green?

- **Instantaneous rollback**: switch load balancer back; no deploy.
- **No half-state**: every request is served by exactly one version.
- **Clean environment**: the new version starts on a fresh
  environment with no leftover state from the old version.
- **Easy to reason about**: each environment is a unit; the rollout
  is a single switchover.

## Why NOT blue-green?

- **Cost**: two full environments running simultaneously.
- **Stateful workloads break**: a database migration that runs on
  green is incompatible with blue. Cross-version sessions, in-flight
  transactions, or sticky state all break.
- **Higher blast radius**: a bug in the new version affects 100%
  of traffic immediately (not 5% as in canary).
- **No gradual confidence-building**: you're all-in on the switchover.

## State machine

```
              ┌──────────────┐
              │ idle         │
              │ (blue live,  │
              │  green idle) │
              └──────┬───────┘
                     │ rollout starts; deploy to green
                     ▼
              ┌──────────────┐
              │ green staged │──── pre-switchover test fails ───→ rollback
              └──────┬───────┘                                           │
                     │ pre-switchover test passes                       │
                     ▼                                                  │
              ┌──────────────┐                                          │
              │ green live   │──── post-switchover health degrades ──→  │
              │ (switchover) │                                           │
              └──────┬───────┘                                           │
                     │ post-switchover health OK                        │
                     ▼                                                  ▼
              ┌──────────────┐                                  ┌──────────────┐
              │ completed    │                                  │ rolled back  │
              │ (tear down   │                                  │ (back to     │
              │  blue)       │                                  │  blue live)  │
              └──────────────┘                                  └──────────────┘
```

Each transition emits a `change` or `contradict` claim signed by the
rollout-shield agent key.

## Pre-switchover validation

Before switching the load balancer to green, run a battery of tests:

| Test | Purpose | Failure mode |
|---|---|---|
| Smoke test | End-to-end happy path | Deploy broken → block switchover |
| Health probes | Service responds | Deploy broken → block switchover |
| Synthetic traffic | Realistic requests | Deploy slow → block switchover |
| Data-layer sanity | DB schema compatible | Migration broken → block switchover |

The tests run against green (idle, no production traffic) and must
all pass before the switchover claim is emitted.

## Post-switchover observation

After the switchover, monitor for a fixed observation window (e.g.,
10 minutes). Health signals (error rate, latency, saturation) must
remain within thresholds; otherwise, automatic rollback is triggered.

The post-switchover window is shorter than canary because the
blast radius is larger and a bug must be caught sooner.

## claim graph for a successful blue-green

```
intent  (release captain → "ship v2.5.0 to prod")
  └─→ change  (CI runner → "deployed v2.5.0 to green; blue still live")
        └─→ test  (pre-switchover suite → "all green; 47/47 passed")
              └─→ change  (CI runner → "switched load balancer; green now live")
                    └─→ test  (post-switchover monitor → "10min p99=138ms; pass")
                          └─→ verify  (release captain → "v2.5.0 in prod; tearing down blue")
                                └─→ change  (CI runner → "blue environment torn down")
```

## claim graph for a failed blue-green

```
intent  (release captain → "ship v2.5.0 to prod")
  └─→ change  (CI runner → "deployed v2.5.0 to green; blue still live")
        └─→ test  (pre-switchover suite → "FAIL: smoke test 3/47 failed (auth)")
              └─→ contradict  (rollout bot → "aborting rollout; pre-switchover failed")
                    └─→ change  (CI runner → "torn down green; blue remains live")
                          └─→ verify  (release captain → "investigating auth regression")
```

## When to use blue-green vs canary

Use **blue-green** when:
- The application is fully stateless (no in-flight state to drain).
- Cost of two environments is acceptable.
- The change is high-stakes (database migration, security patch,
  breaking API change) and you want a clean, single switchover.

Use **canary** when:
- The application has stateful behavior (sessions, in-flight
  transactions).
- Cost of two environments is too high (use one and shift traffic).
- The change is low-risk and gradual exposure is acceptable.

Use **neither** when:
- The application is a dev or staging environment (just deploy).
- The change is purely a documentation update (no deploy at all).
