# Benchmarks

The `benchmarks/` sub-workspace measures the runtime cost of common
rollout-shield operations. This doc is the human-facing overview;
the actual benchmark suite is in `benchmarks/`.

## quick start

```bash
make bench                  # full suite
make bench-micro            # only micro
make bench-ai               # only AI
python -m benchmarks --markdown results/snapshot.md
```

## cost story

Numbers from the snapshot at `benchmarks/results/snapshot.json` (the
exact numbers vary by host; the table below is the *shape* of what
to expect):

| operation | typical cost | budget |
|---|---|---|
| `state.load_config` | < 1 ms | < 5 ms |
| `state.save_config` | < 5 ms | < 20 ms |
| `state.summary` | < 30 ms | < 100 ms |
| `state.iter_claims` (50 claims) | < 5 ms | < 20 ms |
| `keys.new` (Ed25519) | < 5 ms | < 20 ms |
| `health.state_checks` (6 checks) | < 10 ms | < 50 ms |
| `health.repo_checks` (7 checks) | < 50 ms | < 200 ms |
| `health.host_checks` (full) | < 50 ms | < 200 ms |
| `router.warm` (N=2 models) | < 5 ms | < 20 ms |
| `model.warm` | < 1 ms | < 5 ms |
| `own_models.warm` (3 models) | < 50 ms | < 200 ms |

`scripts/install.sh` runs in < 1.5s on this host. The hard build
runtime is dominated by `cp -a` of the package.

## what the benchmarks DO NOT measure

- Network latency (the dashboard server is not exercised in the suite)
- Concurrent load (the suite is single-threaded)
- Cold-start latency of the daemon (the suite runs against
  already-imported modules)

## CI integration

The `tests.yml` workflow runs the smoke benchmark suite on every
push + PR. Full benchmarks are run on a schedule and uploaded to
the workflow artifacts.
