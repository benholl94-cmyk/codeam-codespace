# rollout/

This directory contains the **rollout-pattern** layer of `rollout-shield`.
The patterns describe how to safely promote a code change from "merged
on main" to "running in production", with verifiable evidence at each
step.

The patterns compose with the Claims Protocol (`protocol/`): every
rollout step is captured as a claim (signed by the agent that performed
the step), forming an auditable rollout DAG.

---

## What is a "safe rollout"?

A safe rollout is one where:

1. **The change is known**. The exact diff, the exact commit SHA,
   the exact artifact hash are recorded in a `change` claim.
2. **The change is tested**. Pre-production test runs are captured
   in `test` claims with content hashes of the test output.
3. **The change is staged**. Production traffic is gradually
   routed to the new version (canary, blue-green) rather than
   flipped in a single deploy.
4. **The change is reversible**. A rollback can be executed
   automatically on health-signal degradation, and the rollback
   itself is captured as a claim (`contradict`).
5. **The change is attributable**. Every step is signed by a
   specific agent identity (human or AI), with cryptographic
   provenance.

`rollout-shield` does not implement deployment. It produces the
verifiable evidence that a rollout followed the patterns below.

---

## Patterns

| Pattern | File | Use case |
|---|---|---|
| Canary | `canary-pattern.md` | Low-risk rollouts where gradual exposure is acceptable |
| Blue-green | `blue-green-pattern.md` | High-confidence rollouts where instantaneous switchover is preferred |
| Rollback | `rollback-protocol.md` | Universal: how to roll back safely regardless of the rollout pattern |
| Claim-deploy pipeline | `claim-deploy-pipeline.md` | How the Claims Protocol integrates with the above patterns |

---

## Pattern comparison

| Dimension | Canary | Blue-green |
|---|---|---|
| Resource cost | High (two versions run side-by-side) | Higher (two full environments) |
| Rollback speed | Seconds (route traffic back) | Seconds (switch load balancer) |
| Rollback safety | High (other version kept warm) | Highest (clean environment) |
| Canary fidelity | Medium (small sample) | None (full population or nothing) |
| Suitable for stateful apps | Yes (with care) | No (DB schema changes break) |
| Suitable for stateless apps | Yes | Yes |
| Suitable for long-running sessions | Less ideal (sessions pinned to version) | More ideal (sessions can drain) |

Most teams should default to **canary** for routine rollouts and
**blue-green** for high-stakes rollouts (database migrations,
breaking API changes, security patches).

---

## The claim graph as a rollout ledger

Every rollout step is captured as a claim. The claim graph for a
typical rollout looks like:

```
intent  (human → "ship v2.4.1 to prod")
  └─→ change  (CI runner → "applied diff abc123 to staging")
        └─→ test  (test suite → "staging tests pass; 1024/1024 green")
              └─→ change  (CI runner → "applied same diff to prod-canary")
                    └─→ test  (canary monitor → "5min p99 latency: 142ms (baseline: 138ms)")
                          ├─→ verify  (human supervisor → "canary looks good")
                          │     └─→ change  (CI runner → "promoted canary to 100%")
                          │           └─→ test  (full-traffic monitor → "30min p99: 140ms")
                          │                 └─→ verify  (release captain → "v2.4.1 in prod")
                          └─→ contradict  (rollback bot → "5min error rate exceeded 0.5%; rolling back")
                                └─→ change  (CI runner → "rolled back to v2.4.0")
```

The graph is a complete audit trail. Anyone with the public key
material can verify every step was performed by the agent that
claims to have performed it.

---

## What this directory does NOT contain

- **Deployment automation** (k8s manifests, Terraform modules,
  Ansible playbooks). The patterns describe *what* to do; not
  *how* to do it. Tooling is out of scope.
- **Runtime monitoring configuration** (Prometheus rules, Datadog
  dashboards). Health signals are inputs to the patterns; their
  generation is the monitoring system's responsibility.
- **Incident response procedures** (runbooks, on-call rotations).
  Rollback is one piece of incident response; the rest is owned
  by the SRE team.
