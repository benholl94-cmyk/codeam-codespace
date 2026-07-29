# Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ HOST WORKSPACE (user hardware + software kernel)                     │
│   ~/usr/bin/rollout-shield*         # CLI                            │
│   ~/usr/lib/python/rollout_shield/  # Python package                 │
│   ~/.rollout-shield/                # runtime state (atomic writes)  │
│   ~/.rollout-shield/daemon.json     # monitor heartbeat             │
│   rollout-shield-monitor --daemon    # long-lived background process │
│   curl http://127.0.0.1:8765/        # dashboard                      │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │    cross-cut view
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ REPO WORKSPACE (here, /workspaces/codeam-codespace)                  │
│   rollout_shield/              # core runtime (CLI + daemon + AI)    │
│   dashboard/        ★ NEW      # web UI assets                       │
│   benchmarks/       ★ NEW      # perf benchmarks                     │
│   tests/            ★ NEW      # pytest suite                        │
│   docs/             ★ NEW      # this folder                         │
│   examples/         ★ NEW      # deployment templates                │
│   protocol/  agent/  rollout/  hardware/  # spec dirs                │
│   tools/  codegen/  test/      # primitive specs                      │
│   .github/                      # CI workflows                        │
│   .beads/                       # bd issue tracker (Dolt)            │
│   pyproject.toml                # production-grade packaging          │
│   Makefile                      # convenience targets                 │
│   WORKSPACES.md                 # sub-workspace index                 │
└─────────────────────────────────────────────────────────────────────┘
```

## data flow

```
                  ┌─────────────────────────────────────┐
                  │            AI layer                  │
                  │  ai/router.py     (parallel)        │
                  │  ai/models.py     (mocks + own)     │
                  │  ai/benchmarks.py                   │
                  │  ai/leaderboard.py                  │
                  │  ai/self_cycle.py                   │
                  │  ai/generator.py   (first-of-kind)  │
                  │  ai/own_models.py  (5 own models)   │
                  └─────────────────────────────────────┘
                                  ▲
                                  │ signals (--state, --leaderboard)
                                  │
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
│   CLI (one-shot)     │   │  monitor daemon      │   │   dashboard      │
│   rollout-shield …   │   │  (long-lived)        │   │   http server    │
│   bin/rollout-shield │   │  monitor_daemon.py   │   │   http_server.py │
└──────────────────────┘   └──────────────────────┘   └──────────────────┘
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   ▼
                        ┌──────────────────────┐
                        │    state.py          │
                        │  (atomic JSON ops)   │
                        │  ~/.rollout-shield/  │
                        └──────────────────────┘

                                   ▲
                                   │ observes
                                   │
                        ┌──────────────────────┐
                        │    health checks     │
                        │  health_checks.py    │  state checks
                        │  host_checks.py      │  host kernel
                        │  repo_checks.py      │  repo level
                        │  space.py            │  controller policy
                        └──────────────────────┘
```

## two workspaces

The runtime is intentionally **split across two workspaces**:

- **repo-workspace** = the artifact (this repo). Source code, specs,
  tests, CI. Lives in version control.
- **host workspace** = the runtime substrate. CLI, state, daemon, the
  user's home directory. Lives on the machine.

The split is enforced by `scripts/install.sh` which copies the repo
content into `~/usr/` (the host prefix) and never touches the source
tree. State at `~/.rollout-shield/` lives on the host and is never
in the repo.

## webhook delivery subsystem

A dedicated subsystem at `rollout_shield/webhook_delivery/`
delivers outbound webhooks durably:

```
                       producer (alerter, dashboard, CLI, monitor)
                                       │
                                       ▼
                rollout_shield/webhook_delivery/outbox.py
                  │ atomic per-record write to
                  │ <state_root>/webhooks/deliveries/<id>.json
                  │ + append to outbox/<date>.jsonl event log
                  ▼
                rollout_shield/webhook_delivery/dedupe.py
                  │ per-target idempotency window coalescing
                  ▼
                rollout_shield/webhook_delivery/dispatcher.py
                  │ advisory fcntl/msvcrt lock
                  │ signer.build_headers() ─► HMAC | Ed25519 | none
                  │ urllib POST (configurable timeout)
                  │ retry: 1, 4, 16, 64, 256 s (5 attempts) → DLQ
                  ▼
                <state_root>/webhooks/
                  deliveries/<id>.json     snapshots (atomic-write)
                  dlq/<id>.json            dead-letter
                  stats.json               rolled counters
                  outbox/<date>.jsonl      append-only event audit
```

CLI surface (`rollout-shield webhooks ...`): `target add/list/show/
remove`, `deliver`, `deliveries list/show`, `replay`, `replay-all`,
`drain`, `stats`, `sign-test`, `daemon`.

HTTP API (`/api/webhooks/*`): `targets`, `deliver`, `deliveries`,
`replay`, `stats`, `health`, `sign-test`.

Dashboard: `Webhooks` tab polls every 15s.

Observability: 5 new Prometheus families under
`rollout_shield_webhook_*`; plugin event `webhook.delivered` for
downstream subscribers.

Tests: `tests/test_unit_webhook_delivery.py` (35) +
`tests/test_integration_webhook_delivery.py` (9) +
`tests/test_smoke_webhooks.py` (3, marked `@pytest.mark.smoke`).

Detail: [`docs/WEBHOOKS.md`](WEBHOOKS.md).

## controller policy

A single config field (`controller_policy`) declares who is allowed
to sign claims in the current rollout space:

- `shared` — human + device keys both permitted
- `device-only` — only hardware-anchored keys (the App-controlled Space)
- `human-only` — only non-hardware-anchored keys (dev/test spaces)

Enforced at: `keys new`, `claim create`, every monitor cycle, every
self-heal cycle. See `docs/SECURITY.md` and `rollout_shield/space.py`.

## closed-loop self-heal

When the monitor detects a degraded state, after N cycles it runs
`self-heal` to attempt repairs. Repairs are idempotent + safe (no
delete). Unfixable issues are flagged as "manual action required"
so the operator sees the actionable list.
