# monitoring/

This directory holds the **runtime monitoring** configuration for
rollout-shield beyond the spec docs in `protocol/` and `rollout/`.

## What lives here

- **State directory layout**: `<state_root>/` (default `.rollout-shield/`)
  contains `claims/`, `alerts/`, `health/`, `keys/`, `keys_material/`,
  `reputation.json`, `config.json`, `daemon.json`.
- **Daemon entrypoint**: `python -m rollout_shield monitor_daemon`
  (also reachable via `rollout-shield monitor`).
- **Daemon heartbeat**: `daemon.json` in the state root. Refreshed on
  every cycle. Supervisors (systemd, launchd, Kubernetes liveness
  probes) can read this file to confirm the daemon is making progress.
- **systemd unit example**: see `examples/rollout-shield-monitor.service`
  (forthcoming).
- **launchd plist example**: see `examples/com.rollout-shield.monitor.plist`
  (forthcoming).

## Persistent monitoring

The daemon is **persistent**:

- Runs as a long-lived process (systemd, launchd, Docker, k8s).
- Each cycle runs every enabled health check (see
  `rollout_shield/health_checks.py`).
- All cycle results are appended to daily JSONL files under
  `<state_root>/health/<YYYY-MM-DD>.jsonl`.
- All alerts are appended to daily JSONL files under
  `<state_root>/alerts/<YYYY-MM-DD>.jsonl`.
- Heartbeat is written to `<state_root>/daemon.json` after every cycle.
- On crash, the next start picks up from where it left off — there is
  no in-memory state to lose.

## Health-check definitions

| Check | What it checks | Failure mode |
|---|---|---|
| `state_root_writable` | State directory is writable | Disk full / permission denied / read-only mount |
| `disk_space` | ≥100 MB free on state volume | Disk filling up |
| `recent_claims` | A claim was emitted within the last 24h | Daemon idle; possibly stalled |
| `alert_rate` | ≤10 alerts in the last hour | Flapping; investigate root cause |
| `keys_present` | ≥1 agent key registered | Fresh install not finished |
| `loopback_reachable` | Loopback network is up | Network stack broken |

Add custom checks by extending `health_checks.DEFAULT_CHECKS`. Each
check is a callable taking `State` and returning `HealthResult`.

## Alert dispatch

On health degradation, the daemon dispatches alerts via:

1. Persistent log under `<state_root>/alerts/`
2. Stderr (always; useful for systemd journal / Docker logs)
3. Optional webhook (set `alert_webhook_url` in `<state_root>/config.json`
   or pass `--webhook-url` to the daemon)

A webhook integration can route alerts to:

- **Slack / Discord** — POST a JSON body to the incoming-webhook URL
- **PagerDuty Events API v2** — POST with `event_action: trigger`
- **OpsGenie** — POST with `apiKey`
- **Custom internal service** — any HTTP endpoint that accepts JSON

The alerter is in `rollout_shield/alerter.py` and is intentionally
tiny (~80 lines). It does not deduplicate or rate-limit; that's the
receiver's responsibility.

## Querying the monitoring data

From the CLI:

```bash
# recent alerts
rollout-shield --json claim list  # not quite — use the alerts API instead
```

Or from the dashboard:

```bash
rollout-shield dashboard --port 8765
# browse to http://127.0.0.1:8765/ and click "Alerts"
```

Or directly via curl (the dashboard's API):

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/alerts?limit=20
curl http://127.0.0.1:8765/api/claims?limit=20
curl http://127.0.0.1:8765/api/status
```

## What this directory does NOT contain

- **Production deployment manifests** (k8s, Docker Compose, Helm
  charts). The daemon is a plain Python process — wrap it in your
  runtime of choice.
- **Metrics export** (Prometheus, StatsD). The daemon writes JSON
  state; integrating with a metrics system is a v0.2 roadmap item.
- **Distributed tracing**. Single-process daemon; trace context is
  out of scope.
