# Edge Rollout Testbed Specification

> **Component**: Physical edge devices for canary / blue-green
> rollout rehearsal before production promotion.
> **Purpose**: Catch rollout failures that only manifest under
> realistic load and environmental conditions (network latency,
> power events, hardware-specific bugs).
> **Status**: Concept (not yet implemented; design notes only)

## Why an edge testbed?

Pre-production CI runs in cloud VMs with synthetic load. Production
runs on real hardware with real users, real network conditions, and
real OS quirks. The gap between CI and production is where most
rollout failures hide.

An edge testbed narrows this gap by:

- Running real binaries on representative hardware.
- Driving realistic load patterns (not synthetic requests).
- Exercising the deployment mechanism itself (the rollout pipeline,
  not just the artifact).

## Concept

A small fleet of physical devices (5–20 nodes) that:

1. **Replicate production hardware**. Use the same CPU architecture,
   memory size, storage class (SSD vs HDD), and network interface
   as production nodes.
2. **Run a representative workload**. Either replay production
   traffic or run a synthetic workload that matches production
   patterns.
3. **Are candidates for rollout-shield claims**. Every rollout
   step that touches the testbed (deploy, restart, scale) emits a
   claim into the claim graph. The claims are verifiable.
4. **Report health signals**. Latency, error rate, resource
   saturation, OS-level events (segfaults, OOM kills).

When a rollout is being rehearsed, the rollout pipeline:

- Pushes the new version to N% of testbed nodes (canary).
- Monitors health signals for M minutes.
- If health is acceptable, promotes to next stage; if not, rolls
  back automatically with a `contradict` claim.
- Emits a `verify` or `contradict` claim for each step.

The testbed claims feed into the same reputation system as
production claims, so a rollout that fails on the testbed damages
the agent's reputation just as a production failure would.

## Reference hardware

| Class | Device | Approximate cost | Use case |
|---|---|---|---|
| x86 single-board | Intel NUC, Minisforum | $300–800 | x86 production parity |
| ARM edge | Raspberry Pi 4/5, NVIDIA Jetson Orin | $60–2000 | ARM production parity |
| RPi cluster (5–10 nodes) | Custom rack + PoE switch | $1500–3000 | Small-scale rehearsal |
| Server-class | Dell PowerEdge, HPE ProLiant | $3000+ | High-fidelity rehearsal |

For most teams, a 5-node Raspberry Pi 4 cluster (representative ARM
edge hardware) plus 1–2 Intel NUCs (representative x86 hardware) is
sufficient.

## Testbed integration with rollout-shield

The testbed is a *consumer* of claims, not a producer. Specifically:

- The testbed hosts a small `rollout-shield-agent` instance that
  watches for `change` claims targeting its hostname.
- On seeing a relevant `change` claim, the agent pulls the new
  artifact and deploys it.
- After deployment, the agent emits a `test` claim with the test
  suite results.
- The CI pipeline that orchestrated the rollout waits for `test`
  claims from a quorum of testbed nodes before promoting to
  production.

This composes naturally with the existing Claims Protocol: the
testbed is just another agent in the graph, with its own reputation.

## Threat model

| Adversary | Capability | Testbed mitigation |
|---|---|---|
| Bad rollout that passes CI | Production outage | Testbed catches hardware-specific failures |
| Compromised testbed agent | False `verify` claims | Reputation penalty; cross-agent quorum required |
| Testbed hardware failure | False-positive rollback | Health signals separate from claim validity |

The testbed is not a security boundary. It is a *quality boundary*.

## Open questions

1. **How many testbed nodes for statistical significance?** Answer
   depends on rollout variability; start with 5 nodes and tune.
2. **Real traffic vs synthetic?** Synthetic is cheaper but may
   miss rare production-only paths. Recommendation: synthetic
   baseline + periodic production-traffic replay (with privacy
   scrubbing).
3. **Edge-cloud integration?** Some rollouts push code from cloud
   CI to edge devices. The claim graph spans both. Documenting
   this hybrid topology is on the v0.2 roadmap.

## Status

This document is a *concept*, not an implementation. No reference
hardware is provisioned in this repository. The first concrete
deployment will be a documentation-and-recipe effort (a `hardware/
recipes/` directory) that walks through provisioning a 5-node Pi
cluster.
