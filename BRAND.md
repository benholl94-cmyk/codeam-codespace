# Brand Guide: rollout-shield

> **Product brand**: rollout-shield
> **Category**: repo-safe-rollouts
> **Tagline**: "Every rollout, provably attributable."
> **Status**: Brand v0.1 — additive to existing repo; not a renaming of files.

---

## 1. Name and identity

**Product name**: `rollout-shield`
- Lowercase, hyphenated (matches the codebase convention: `codeam-codespace`,
  `agent-card`, `protocol-claim`).
- Reads as a single token (`rolloutshield`) when grepped.
- No version suffix in the name itself; versioning happens at the protocol
  level (`beads-claims-v0.1`, `agent-card-v0.1`).

**Pronunciation**: /ˈroʊl.aʊt ʃiːld/ — "roll-out shield". Two syllables,
not three.

**What it is**: a hardware-and-software platform that makes every step
of a code rollout *provably attributable* to a specific agent identity,
with cryptographic claims and a portable reputation layer.

**What it is NOT**:
- Not a CI/CD system. `rollout-shield` does not replace GitHub Actions,
  GitLab CI, or any other CI; it adds a verifiable claim layer on top.
- Not a deployment tool. `rollout-shield` does not push code to
  production; it produces the verifiable evidence that a rollout is
  safe to push.
- Not a security product. `rollout-shield` defends against accidental
  lack of provenance, not against malicious insiders.

---

## 2. Tagline

**Primary tagline**: "Every rollout, provably attributable."

**Alternates** (for A/B testing or contextual use):
- "Trust the rollout, not the output."
- "Claims, not logs, for deploy evidence."
- "Hardware-rooted provenance for every commit."
- "Reputation that survives across repos."

The primary tagline is the canonical form. Alternatives are permitted
in marketing copy but should not appear in product surfaces (README,
docs, code identifiers).

---

## 3. Voice and tone

**Voice**: precise, engineering-grade, evidence-first.

**Tone**: confident without hype. We make claims we can prove.

**Forbidden**:
- "Revolutionary", "game-changing", "next-generation", "disruptive",
  "10x", "magical", "blazing-fast" — any superlative that we cannot
  back up with measurement.
- "AI-powered" as a primary descriptor — every AI competitor uses it.
- Anthropomorphization of the agent — no "the agent thinks",
  "the agent believes", "the agent decides".

**Permitted**:
- "Cryptographically attested"
- "Verifiable end-to-end"
- "Locally auditable"
- "Hardware-rooted"
- "Reputation-portable"
- "Append-only DAG"
- "Schema-validated"
- "Local-first"
- "Provider-agnostic"

---

## 4. Visual identity

The product uses no logo yet (logo design is a separate work item).
In text surfaces, the brand is rendered as plain lowercase text:
`rollout-shield`. In headings or emphasis, it MAY be rendered as
`rollout-shield` (still lowercase, no capitalization).

Do NOT use:
- CamelCase: `RolloutShield`, `rolloutShield`
- Underscore: `rollout_shield`
- Spaced: `rollout shield`
- All-caps: `ROLLOUT-SHIELD`

The protocol layer is referred to as "the Claims Protocol" (capitalized)
or "beads-claims-v0.1" (lowercase, hyphenated, versioned). Never "the
rollout-shield protocol" or "the rollout protocol" — those are
ambiguous with industry terms.

---

## 5. Positioning

### 5.1 The problem space

In 2026, AI coding agents produce code faster than humans can verify
(see `docs/competitive-analysis.md` §2). The bottleneck is not
generation; it is *evidence*. Every existing CI/CD system produces
artifacts (logs, test results, signed commits) but none produces a
*structured, addressable, signed claim* that ties the rollout to
the agent that produced it.

### 5.2 The wedge

`rollout-shield` occupies a unique position in the CI/CD landscape:
it is the only system that produces **claim graphs** as a first-class
artifact. Competing products produce logs (Splunk, Datadog), metrics
(Prometheus), or signed commits (Git itself). None produces a typed,
schema-validated, cryptographic DAG that links intents → changes →
tests → verifications → contradictions → delegations.

### 5.3 Hardware+Software framing

Unlike competing products that are software-only or hardware-only,
`rollout-shield` integrates both layers:

- **Software layer**: Claims Protocol, agent identity, reputation
  model, devcontainer template, GitHub Actions workflows, scripts,
  tools.
- **Hardware layer**: TPM 2.0 for Ed25519 private-key storage, HSM
  for production claim signing, edge testbeds for rollout rehearsal
  (canary / blue-green verification).

The hardware layer is *advisory* — the software layer runs end-to-end
without it. The hardware layer *raises the trust ceiling* by rooting
the agent's signing key in tamper-resistant silicon.

---

## 6. Naming conventions in code

| Surface | Convention | Example |
|---|---|---|
| File names | lowercase, hyphenated | `rollout-shield.md`, `canary-pattern.md` |
| Schema names | lowercase, hyphenated, versioned | `rollout-claim-v0.1.schema.json` |
| CLI commands | lowercase, hyphenated | `rollout-shield verify <claim>` |
| Env vars | UPPER_SNAKE | `ROLLOUT_SHIELD_HOME` |
| Beads labels | lowercase, hyphenated | `rollout-shield`, `canary-deploy` |
| Git branch prefixes | kebab-case | `rollout-shield/`, `canary/`, `fix/` |

---

## 7. Co-branding rules

`rollout-shield` composes with the following adjacent technologies,
each of which has its own brand identity:

| Adjacent tech | Co-brand language |
|---|---|
| Beads (`bd`) | "rollout-shield on Beads" or "Beads + rollout-shield" |
| Git | "rollout-shield produces Git commits with attached claims" |
| GitHub Actions | "rollout-shield runs as a GitHub Actions workflow" |
| OpenTPM / tpm2-tss | "rollout-shield with TPM 2.0 key storage" |
| PKCS#11 / YubiHSM | "rollout-shield with HSM-backed signing" |

In all cases, `rollout-shield` is the primary brand and the adjacent
technology is in its conventional form. Never rebrand an adjacent
technology to match.

---

## 8. Trademark and proprietary marks

The name `rollout-shield` is the proprietary mark of this repository's
author. See `LICENSE` and `COPYRIGHT.md` for the full proprietary
posture.

The protocol name "Claims Protocol" is a generic descriptive term
used in the industry; it is not a proprietary mark. The versioned
identifier `beads-claims-v0.1` is specific to this implementation
and is the canonical way to refer to the protocol in code and
schemas.
