# Runbooks

Operator playbooks for common failure modes. Each runbook lists the
symptom, the diagnostic command(s), and the recovery steps.

## RB-001 — monitor reports `degraded`

**Symptom**: `rollout-shield status` shows `latest health: degraded`
or any single check returns `unhealthy`.

**Diagnose**:

```bash
rollout-shield self-check --json | jq
```

Read the `checks[]` array. Each check has `name`, `ok`, and
`details`. The failing check's name maps to a code path:

| check | where to look |
|---|---|
| `state-writable` | `~/.rollout-shield/` permissions, disk full |
| `keys-present` | at least one key in `keys/` |
| `recent-claims` | claim log is being written |
| `host-load` | `~/.rollout-shield/host_checks.py` |
| `repo-clean` | `rollout-shield self-heal --dry-run` |

**Recover**:

```bash
# 1. try the safe auto-repair (no delete)
rollout-shield self-heal --dry-run
rollout-shield self-heal

# 2. if that fails, run the scratch-state smoke test to confirm
#    the runtime itself is healthy
rollout-shield self-test

# 3. if self-test passes but live state is broken, restore from
#    the most recent backup
tar xzf ~/.rollout-shield/backup-*.tar.gz -C ~/
```

## RB-002 — daemon exits repeatedly (systemd)

**Symptom**: `systemctl --user status rollout-shield` shows
`Active: failed` and restart-count keeps growing.

**Diagnose**:

```bash
journalctl --user -u rollout-shield -n 200 --no-pager
```

Look for the last 5 fatal log lines. Common causes:

- **Port already in use**: change `--port` in the systemd unit
- **State directory missing**: re-run `rollout-shield install`
- **JSONL corruption**: see RB-003

**Recover**:

```bash
systemctl --user stop rollout-shield
bash scripts/verify-install.sh
systemctl --user start rollout-shield
```

## RB-003 — JSONL claim log corruption

**Symptom**: `rollout-shield claim list` returns 0 claims, but the
file at `~/.rollout-shield/claims.jsonl` is non-empty.

**Recover**:

```bash
# 1. find the last good line (first corrupt byte)
python3 -c "
import json
ok = 0
with open('$HOME/.rollout-shield/claims.jsonl','rb') as fh:
    for i, line in enumerate(fh):
        try: json.loads(line); ok += 1
        except json.JSONDecodeError: print('corrupt at line', i+1); break
print('good lines:', ok)
"

# 2. truncate to the last good line (idempotent, safe)
python3 -c "
n = $OK  # from above
with open('$HOME/.rollout-shield/claims.jsonl','rb') as fh:
    lines = fh.readlines()
with open('$HOME/.rollout-shield/claims.jsonl','wb') as fh:
    fh.writelines(lines[:n])
"
```

## RB-004 — controller policy wrong

**Symptom**: signing fails with `controller_policy_mismatch`.

**Recover**:

```bash
rollout-shield space show
rollout-shield space set-policy <desired> --yes
rollout-shield space validate
```

The previous config is backed up to `config.json.bak.<ts>` so you
can revert with `cp config.json.bak.<ts> config.json`.

## RB-005 — metrics endpoint unreachable

**Symptom**: Prometheus shows `up == 0` for the rollout-shield
target.

**Diagnose**:

```bash
curl -fsS http://127.0.0.1:8765/api/metrics | head -10
```

**Recover**:

1. Confirm the dashboard is running:
   `rollout-shield dashboard status`
2. Confirm the bind address: `--host 127.0.0.1 --port 8765`
3. If behind a reverse proxy, confirm the proxy forwards `/api/metrics`
4. If you bind to a non-loopback address, also open the port in
   the host firewall

## RB-006 — slow AI router

**Symptom**: `rollout-shield ai route` takes > 1s per call.

**Diagnose**:

```bash
rollout-shield metrics | grep router_latency
```

If `router_latency_seconds_bucket{le="0.5"}` is far from the count,
the slowest model is dominating the tail.

**Recover**:

1. Reduce model count: `rollout-shield ai route <prompt> --model mock:echo --model mock:expand`
2. Switch to `consensus` strategy (drops the slowest model)
3. Add `max_workers` knob (default 4; try 8 on more cores)

## RB-007 — `bd doctor` complains about issues.jsonl

**Symptom**: `bd doctor` returns non-zero.

**Recover**:

```bash
bd doctor --verbose
bd lint
bd orphans
# Most issues resolve with a `bd rebase`; see bd docs.
```

## RB-008 — install overwrites a running daemon's keys

**Symptom**: re-running `scripts/install.sh` doesn't change keys but
the daemon now sees a new keyring because `$PREFIX/bin/rollout-shield`
re-imports `rollout_shield`.

**Recover**:

```bash
# 1. stop the daemon so it doesn't see the new keys mid-cycle
systemctl --user stop rollout-shield
# 2. install fresh
bash scripts/install.sh
# 3. start the daemon again
systemctl --user start rollout-shield
```

## RB-009 — `rollout-shield routing` shows `developer` instead of `government`

**Symptom**: the smart-routing manifest doesn't seem to be loaded.

**Diagnose**:

```bash
ls -la ~/usr/etc/rollout-shield/smart-routing.json
cat ~/usr/etc/rollout-shield/smart-routing.json | jq .build_tier
```

If the file is missing or `build_tier` ≠ `government`, the install
script didn't run OR was run with `--prefix` pointing somewhere
other than `~/usr/`.

**Recover**:

```bash
bash scripts/install.sh
~/usr/bin/rollout-shield routing
```