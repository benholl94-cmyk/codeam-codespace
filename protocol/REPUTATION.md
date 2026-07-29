# Reputation Protocol

> **Companion to**: `README.md` (Claims Protocol)
> **Status**: Specification draft

## Purpose

Define how agent reputation is derived from the claim graph produced by
the Claims Protocol (`README.md`), so that:

1. Reputation is **portable** across repos (an agent's track record
   in repo A informs trust in repo B)
2. Reputation is **evidence-based** (computed from verified claims,
   not from self-reported scores)
3. Reputation is **incremental** (improves with positive verifications,
   degrades with contradictions, but never resets to zero)

## Reputation variables

For each agent `a`, the reputation system tracks:

| Variable | Symbol | Range | Meaning |
|---|---|---|---|
| Verified claims | `V(a)` | ℕ | Count of `change` claims by `a` that received `verify` |
| Contradicted claims | `C(a)` | ℕ | Count of `change` claims by `a` that received `contradict` |
| Unresolved claims | `U(a)` | ℕ | Count of `change` claims by `a` with no verdict yet |
| Issue closures | `I(a)` | ℕ | Count of Beads issues `a` drove to `closed` status |
| Tenure | `T(a)` | days | Age of `a`'s oldest claim |

## Score formula

```
R(a) = (V(a) − 2·C(a)) / (1 + U(a)) · log(1 + T(a)/30)
```

**Interpretation:**

- The numerator rewards verified claims and penalizes contradictions
  twice as heavily (one contradiction is worse than one missing
  verification).
- The denominator dampens the score when there are many unresolved
  claims, preventing agents from gaming reputation by issuing many
  claims and abandoning them.
- The `log(1 + T(a)/30)` factor rewards longevity: an agent that has
  been operating for 6 months gets a multiplicative bonus of
  `log(2) ≈ 0.69`; for 12 months, `log(3) ≈ 1.10`. The 30-day
  constant means brand-new agents start without the bonus and must
  earn it.

**Range**: `R(a) ∈ (−∞, +∞)`. In practice, well-behaved agents sit
in `R(a) ∈ [0, 100]` after a few months of activity.

## Reputation states

| Range | State | Implication |
|---|---|---|
| `R(a) > 50` | `trusted` | Other agents may consume `change` claims without review |
| `10 ≤ R(a) ≤ 50` | `established` | Claims require routine spot-check |
| `0 ≤ R(a) < 10` | `probationary` | Claims require full review |
| `R(a) < 0` | `untrusted` | Claims must be re-verified by a third party |

States are advisory; they do not gate commits. A repo's CI may choose
to enforce stricter checks for lower-state agents.

## Cross-repo portability

Reputation is keyed by agent identity (`agent/identity.json`), not by
repository. The first time an agent commits to repo B, the system
imports the agent's existing reputation from the local claim graph
(synced via `bd dolt pull` or equivalent).

**Privacy**: the import includes only the agent's aggregate score and
claim counts, not the contents of the claims. This is sufficient for
reputation computation while preserving confidentiality of the
underlying work.

**New repos with no prior history**: an agent's reputation starts at
`R = 0` (probationary) on a fresh repo. It grows as verified claims
accumulate.

## Anti-patterns the protocol explicitly rejects

- **Reputation buying**: reputation is computed from the claim graph,
  not from any external signal (followers, endorsements, payments).
- **Reputation decay without cause**: tenure only multiplies existing
  verified/contradicted claims. An idle agent does not lose
  reputation; it simply does not gain any.
- **Self-assessment**: agents cannot edit their own reputation. Only
  verified claims from the graph modify `R(a)`.
- **Reputation laundering**: contradictions can never be "erased" by
  subsequent verifications. The score formula accumulates `C(a)`
  indefinitely, so a single serious failure permanently scars the
  agent's history.

## Open questions

1. **Negative reputation transfer**: if agent A delegates to agent B
   and B fails, does A's reputation also drop? Current spec: yes,
   proportional to the share of failed delegations A initiated.
2. **Collaborative reputation**: if two agents co-author a claim,
   does the reward split? Current spec: split 50/50; configurable
   per-claim via a `contributors` field.
3. **Reputation reset on key rotation**: if an agent rotates its
   signing key, does reputation transfer? Current spec: yes, by
   including a `key_history` block in `agent/identity.json` that
   proves continuity.
