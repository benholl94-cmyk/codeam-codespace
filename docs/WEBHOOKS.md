# Webhook Delivery Subsystem

`rollout-shield` ships a **production-grade webhook delivery subsystem**
that turns outbound webhook intents into durable, signed, retried,
deduplicated, observable HTTP deliveries. It is built around the
**outbox pattern**: every delivery is persisted to state *before* any
HTTP attempt, so a crash mid-flight never loses an event.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       webhook delivery pipeline                           │
└──────────────────────────────────────────────────────────────────────────┘

   producer ─► enqueue(...)
                  │
                  ▼
   ┌──────────────────────────┐    atomic JSON write
   │  <state>/webhooks/       │ ◄─────────────────────┐
   │     deliveries/<id>.json │                        │
   │     outbox/<date>.jsonl  │  append-only event log │
   └──────────────────────────┘                        │
                                                       │
   ┌──────────────────────────────────────┐             │
   │  dispatcher.run_once()  (or CLI     │             │
   │  `webhooks drain`, or the monitor   │             │
   │  daemon)                            │             │
   └──────────────┬───────────────────────┘             │
                  │                                     │
                  ▼                                     │
   cross-process fcntl/msvcrt lock on .lock             │
                  │                                     │
                  ▼                                     │
   signer.build_headers(target, payload)  ───► HMAC ──► │
                                              Ed25519 ──►│
                  │                                     │
                  ▼                                     │
   http POST (stdlib urllib, 10s default timeout)       │
                  │                                     │
       success | 5xx/network | 4xx                      │
            │           │             │                 │
            ▼           ▼             ▼                 │
        delivered    attempt      mark FAILED           │
        + stats      bumped       (terminal)            │
            │           │                               │
            │           ▼                               │
            │      next_attempt_at = now + backoff[i]   │
            │           │                               │
            │           ▼ (after max attempts)          │
            │        mark DLQ + copy to dlq/<id>.json   │
            │           │                               │
            └───────────┴────────► atomic status writes ─┘
```

## Why a webhook subsystem?

The pre-existing **alerter** dispatched alerts inline, best-effort, with
no retry, no dedupe, no signing, and no observability. That meant a
flapping receiver or a brief network blip would silently drop
notifications. This subsystem replaces that code path with a durable,
tested pipeline that integrates with the existing
signing-keys/state/metrics/monitoring infrastructure.

## Quick start

```bash
# 1. Register a target
rollout-shield webhooks target add my-receiver \
    https://example.com/hooks/rollout \
    --sign-mode hmac \
    --signing-key "shared-secret-or-key-id"

# 2. Deliver a payload (returns delivery_id)
rollout-shield webhooks deliver --target my-receiver \
    --payload '{"event":"canary_promoted","stage":"canary-50","sha":"abc123"}'

# 3. Drain pending deliveries now
rollout-shield webhooks drain

# 4. Inspect
rollout-shield webhooks deliveries list
rollout-shield webhooks deliveries show <delivery-id>
rollout-shield webhooks stats
```

A typical CronMonitor + webhook integration:

```cron
*/1 * * * * rollout-shield webhooks drain --max 50 >> ~/.rollout-shield/webhook-drain.log 2>&1
```

For a long-lived background drainer, run the dedicated daemon:

```bash
rollout-shield webhooks daemon   # blocks, drains every 5s
```

The existing monitor daemon will also opportunistically call
`run_once()` when the webhooks subsystem is enabled — see
`rollout-shield webhooks enable-monitor`.

## Signing modes

| Mode | Header | Key material | Receiver verifies by |
|---|---|---|---|
| `none` | `X-Rollout-Shield-Signature: none` | none | N/A |
| `hmac` | `X-Rollout-Shield-Signature: sha256=<hex>` | shared secret (string) | recomputing HMAC over canonical payload |
| `ed25519` | `X-Rollout-Shield-Signature: ed25519=<base64>` | registered key (by `key_id`) | looking up public key by `key_id` and verifying |

Every delivery carries these audit headers:

```
Content-Type: application/json
User-Agent: rollout-shield-webhooks/1.0
X-Rollout-Shield-Timestamp: <unix-seconds>
X-Rollout-Shield-Signature: <mode-specific>
X-Rollout-Shield-Key-Id: <signing_key (truncated)>     # hmac / ed25519 only
```

The signature is computed over the **canonical payload** (sorted keys,
no whitespace — same algorithm as the claims protocol). Receivers MUST
canonicalize the body before verifying.

### Quick verification snippet (Python receiver)

```python
import hmac, hashlib, json
SECRET = b"shared-secret-or-key-id"
expected = hmac.new(SECRET, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
assert request.headers["X-Rollout-Shield-Signature"] == "sha256=" + expected
```

## State layout

All webhook state lives under `~/.rollout-shield/webhooks/`:

```
webhooks/
├── targets/<name>.json          # registered delivery targets
├── deliveries/<delivery-id>.json  # canonical current snapshot
├── dlq/<delivery-id>.json       # dead-letter copies
├── outbox/<YYYY-MM-DD>.jsonl    # append-only event log
├── stats.json                    # rolled counters
└── .lock                          # advisory fcntl/msvcrt cross-process lock
```

Every state transition (`enqueued`, `status_changed`, `dlq_moved`,
`replayed`) emits one JSON row to the daily outbox log — this is the
audit trail.

## Retry / dedupe / DLQ semantics

### Retry schedule (exponential)

| attempt | delay before | total elapsed |
|---|---|---|
| 1 | 0s | 0s |
| 2 | 1s | 1s |
| 3 | 4s | 5s |
| 4 | 16s | 21s |
| 5 | 64s | 85s |
| 6 | 256s | 341s |

Per-target `max_attempts` and `backoff_seconds` override the defaults
at registration time. After `max_attempts` the delivery is **moved to
`dlq/`** and the next `replay` becomes manual.

Retryable HTTP responses: `0` (network error), `408`, `429`, `5xx`.
Non-retryable: `2xx` (delivered), `4xx-other` (marked `failed`
permanently).

### Dedupe

A delivery is a duplicate of an existing one when **all three** match:

- `target_name`
- `idempotency_key` (caller-supplied or auto-generated as `auto-<rand>`)
- `payload_sha256` (over the canonical payload)

…**and** the existing record's `updated_at` is within the target's
`dedupe_window_seconds` (default 300s = 5 min). When matched,
`enqueue()` returns the **existing** `delivery_id` instead of creating
a new one — so retries from a buggy producer are harmless.

### Circuit breaker

A target with `fail_streak >= threshold` (default 10 consecutive
failures) becomes **paused** for `cooldown_seconds` (default 300s). The
dispatcher will skip paused targets. A successful delivery resets
the streak.

### Replay

Only `dlq` and `failed` deliveries can be replayed. Replay creates a
**new attempt** but preserves the original delivery record and
increments `replayed_total`. Replaying an already `delivered` record
is rejected with `ReplayError`.

## HTTP API

The dashboard HTTP server (default `:8765`) exposes a read-mostly API:

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/webhooks/targets` | list registered targets |
| GET  | `/api/webhooks/targets/<name>` | one target |
| POST | `/api/webhooks/targets` | register (idempotent) |
| DELETE | `/api/webhooks/targets/<name>` | remove |
| POST | `/api/webhooks/deliver` | enqueue a delivery |
| GET  | `/api/webhooks/deliveries?status=dlq&target=foo` | filtered list |
| GET  | `/api/webhooks/deliveries/<id>` | one delivery |
| POST | `/api/webhooks/deliveries/<id>/replay` | manual replay |
| GET  | `/api/webhooks/stats` | counters + depth |
| GET  | `/api/webhooks/health` | liveness + backpressure (oldest pending age) |

The dashboard's **Webhooks** tab polls `/stats`, `/deliveries`,
`/targets` every 15s.

## Metrics

Five Prometheus-style families are registered (visible via
`rollout-shield metrics`):

| Family | Type | Labels |
|---|---|---|
| `rollout_shield_webhook_deliveries_total` | Counter | `target`, `status` |
| `rollout_shield_webhook_delivery_attempts_total` | Counter | `target`, `result` (success/4xx/5xx/network) |
| `rollout_shield_webhook_delivery_duration_seconds` | Histogram | `target`, `result` |
| `rollout_shield_webhook_outbox_depth` | Gauge | — |
| `rollout_shield_webhook_dlq_depth` | Gauge | — |

Health alerts can be wired on
`rollout_shield_webhook_dlq_depth > 0` for any non-trivial duration.

## Plugin hook

Plugins can subscribe to `webhook.delivered` (fired after every
successful HTTP 2xx) or `webhook.failed` (fired before DLQ move). See
`rollout_shield/commands/plugins.py` and the example plugin under
`examples/plugins/`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `TargetError: target name is required` | empty `--name` | supply a non-empty DNS-label name |
| `OutboxError: target_name is required` | empty `--target` | supply a registered target name |
| every attempt returns `503` | receiver is down or rate-limited | check `webhooks stats`, increase `max_attempts` or backoff |
| status stuck at `pending` forever | target is **paused** (circuit breaker tripped) | wait for cooldown, or `target update --enable` |
| DLQ depth keeps growing | receiver is permanently dead | investigate receiver, then `webhooks replay-all --status dlq` after fix |
| signature header missing | `sign-mode none` | set `--sign-mode hmac --signing-key <secret>` |
| `ReplayError: already delivered` | tried to replay a successful delivery | only `dlq` / `failed` records can be replayed |

## Design decisions

- **Atomic per-record writes** — every status transition uses
  `state.atomic_write_json` (write-temp + fsync + rename). A crash mid-
  write cannot corrupt a delivery snapshot.
- **Event log not the source of truth** — the per-delivery JSON is
  canonical. The event log is for audit/diff/playback.
- **Advisory cross-process lock** — `dispatcher.run_once` acquires a
  per-state-root `fcntl.flock` (POSIX) / `msvcrt.locking` (Windows).
  Concurrent drainers cooperate instead of double-sending.
- **Stdlib HTTP** — `urllib.request` only. No `requests` /
  `httpx` dependency to bloat the install footprint.
- **Signing is optional, signed-by-default** — `sign-mode none` is
  legal (and useful for local development), but every production
  target SHOULD use `hmac` or `ed25519`.
- **No background thread** — the subsystem is fully driven by
  `webhooks drain` or the daemon. Operators control the drain cadence.

## CLI reference

```
rollout-shield webhooks target add <name> <url>
    [--sign-mode {none,hmac,ed25519}]
    [--signing-key <key>]
    [--max-attempts N] [--timeout-seconds N]
    [--enable | --disable]
    [--dedupe-window-seconds N]
    [--fail-threshold N] [--cooldown-seconds N]

rollout-shield webhooks target list [--json]
rollout-shield webhooks target remove <name>
rollout-shield webhooks target show <name>

rollout-shield webhooks deliver --target <name>
    --payload <json>
    [--idempotency-key <key>]
    [--trace-id <id>]

rollout-shield webhooks deliveries list
    [--status {pending,in_flight,delivered,failed,dlq,cancelled}]
    [--target <name>]
    [--limit N]
    [--json]

rollout-shield webhooks deliveries show <delivery-id> [--json]

rollout-shield webhooks replay <delivery-id>
rollout-shield webhooks replay-all [--status dlq]

rollout-shield webhooks drain [--max N] [--json]

rollout-shield webhooks stats [--json]

rollout-shield webhooks sign-test --target <name> --payload <json>
    # prints canonical headers + signature for debugging receivers

rollout-shield webhooks daemon [--interval-seconds N]
    # long-running drain loop

rollout-shield webhooks enable-monitor | disable-monitor
    # wire the subsystem into the existing monitor daemon
```

## See also

- `docs/ARCHITECTURE.md` — overall system picture
- `docs/SECURITY.md` — signing + replay-attack considerations
- `examples/config/webhooks.json` — example target file
- `tests/test_unit_webhook_delivery.py` — unit coverage (35 tests)
- `tests/test_integration_webhook_delivery.py` — E2E with mock receiver (9 tests)
- `tests/test_smoke_webhooks.py` — installed-CLI smoke (3 tests)
