# Claim-Deploy Pipeline

> **Pattern**: How the Claims Protocol integrates with the rollout
> patterns (canary, blue-green, direct) to produce a verifiable
> rollout DAG.
> **Status**: Pattern specification v0.1
> **Audience**: Engineers wiring rollout-shield claims into their
> existing CI/CD pipelines.

## The integration shape

`rollout-shield` does **not** deploy code. It **observes** the
deployment pipeline and emits claims at each observable step. The
shape is:

```
   existing CI/CD pipeline                rollout-shield
  ┌──────────────────────┐         ┌──────────────────────────┐
  │ 1. CI runs tests     │ ──────► │ emits `test` claim       │
  │ 2. Build artifact    │ ──────► │ emits `change` claim     │
  │ 3. Push to staging   │ ──────��� │ emits `change` claim     │
  │ 4. Run E2E tests     │ ──────► │ emits `test` claim       │
  │ 5. Canary deploy     │ ──────► │ emits `change` claim     │
  │ 6. Monitor canary    │ ──────► │ emits `test` claim       │
  │ 7. Promote to prod   │ ──────► │ emits `change` claim     │
  │ 8. Monitor prod      │ ──────► │ emits `test` claim       │
  │ 9. Mark complete     │ ──────► │ emits `verify` claim     │
  └──────────────────────┘         └──────────────────────────┘
```

The CI/CD pipeline is unchanged. The rollout-shield agent is a
sidecar that observes deployment events (via webhooks, log
scraping, or API polling) and emits claims accordingly.

## Three integration patterns

### Pattern A: Webhook integration (preferred)

The CI/CD pipeline emits deployment events as webhooks to a
rollout-shield-agent endpoint. The agent signs each event and
appends it to the claim graph.

```
CI/CD  ──►  webhook  ──►  rollout-shield-agent  ──►  claim graph
```

Pros:
- Real-time (sub-second latency from event to claim).
- No polling overhead.
- Decoupled from CI/CD internals.

Cons:
- Requires the CI/CD platform to support outbound webhooks
  (most do: GitHub Actions, GitLab CI, Jenkins, CircleCI all
  support webhooks on job lifecycle events).

### Pattern B: Log-scraping integration (fallback)

The rollout-shield-agent scrapes CI/CD logs (typically written to
a shared volume or pushed to a log aggregator) and emits claims
based on log patterns.

```
CI/CD  ──►  logs  ──►  rollout-shield-agent  ──►  claim graph
```

Pros:
- Works with any CI/CD system (no webhook support required).
- Can retroactively ingest historical deployments.

Cons:
- Higher latency (polling interval, typically 30s–5min).
- Brittle to log format changes.
- Requires pattern definitions per CI/CD platform.

### Pattern C: CLI invocation (explicit)

The CI/CD pipeline invokes the rollout-shield CLI directly as
a step in its job:

```yaml
- name: rollout-shield claim
  run: |
    rollout-shield claim change \
      --beads-issue "$BEADS_ISSUE_ID" \
      --diff-hash "$(git rev-parse HEAD^{tree})" \
      --files-touched "$FILES_TOUCHED"
```

Pros:
- Explicit; the CI/CD author controls exactly which events become
  claims.
- Works offline (no network needed at claim-signing time).

Cons:
- Requires rollout-shield CLI installed in the CI environment.
- Tight coupling between CI/CD job config and rollout-shield.

## Recommended default: Pattern A

For most modern CI/CD platforms (GitHub Actions, GitLab CI, Buildkite,
CircleCI), webhook integration is the lowest-friction default. The
rollout-shield-agent receives the webhooks, validates them against
the platform's signing key, and emits claims.

## Reference wiring: GitHub Actions

```yaml
# In the GitHub Actions workflow that performs the deployment
- name: Notify rollout-shield
  if: always()
  run: |
    curl -fsSL -X POST "${ROLLOUT_SHIELD_AGENT_URL}/webhook" \
      -H "Authorization: Bearer ${ROLLOUT_SHIELD_AGENT_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
        "event": "'"${{ steps.deploy.outcome }}"'",
        "job": "'"${{ github.job }}"'",
        "run_id": "'"${{ github.run_id }}"'",
        "sha": "'"${{ github.sha }}"'",
        "beads_issue_id": "'"${{ env.BEADS_ISSUE_ID }}"'"
      }'
```

The rollout-shield-agent on the receiving end:

1. Validates the webhook signature (using GitHub's webhook secret).
2. Translates the event into a claim body.
3. Signs the claim with the agent's Ed25519 key.
4. Appends to `.beads/claims/<agent_id>/<date>.jsonl`.
5. (Optional) Updates the corresponding Beads issue's status.

## Cross-agent verification

A claim is only as strong as its verification. For high-stakes
rollouts, the claim graph should include cross-agent `verify`
claims:

```
test (CI runner)
  └─→ verify (human supervisor)
        └─→ change (production deploy)
              └─→ test (production monitor)
                    └─→ verify (release captain)
```

The `verify` claim is forbidden from being authored by the same
agent that authored the original claim (per `protocol/README.md`
§ Anti-patterns). This prevents self-verification.

For a canary rollout, the cross-agent quorum is typically:

- 1 CI runner `change` claim
- 1 monitor `test` claim
- 1 human supervisor `verify` claim
- (Optional) 1 second human supervisor `verify` claim (M-of-N quorum)

Only when all three required claims exist does the CI pipeline
promote to the next canary stage.

## Storage and retention

Claims are stored in `.beads/claims/<agent_id>/<YYYY-MM>.jsonl`,
one file per agent per month. Each file is append-only.

Retention policy:

- Hot storage: claims from the last 90 days are queryable in
  real time via `rollout-shield query`.
- Cold storage: older claims are archived to
  `.beads/claims/archive/<YYYY>/<agent_id>.jsonl.gz` (gzip-compressed,
  gitignored). They remain verifiable; just slower to query.
- Default retention: 7 years (typical for compliance). Configurable
  via `rollout-shield config retention`.

## What this pattern does NOT cover

- **Cross-repo rollouts**. A deployment that spans multiple
  repositories (microservices, polyrepo) is a v0.2 roadmap item.
- **Multi-region rollouts**. Region-by-region rollout claims are
  supported (each region is a separate `change` claim); the
  coordination between regions is the rollout pipeline's
  responsibility.
- **Customer-facing rollback notifications**. Status-page updates
  and customer notifications are owned by the comms team; the
  claim graph is the input but not the output.
