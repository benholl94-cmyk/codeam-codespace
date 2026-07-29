# benchmarks sub-workspace

Performance benchmarks for the rollout-shield runtime. The numbers here
are the **cost story** — they tell operators how much time + memory the
runtime consumes under realistic loads.

## running

```bash
# full suite
make bench

# narrower
make bench-micro
make bench-ai

# explicit module
python -m benchmarks
python -m benchmarks --kind micro
python -m benchmarks --kind ai --output results/snapshot.json --markdown results/snapshot.md
```

## what's measured

### micro

| bench | what it measures |
|---|---|
| `state.load_config` | JSON load + parse of `config.json` |
| `state.save_config` | atomic write + temp-rename of `config.json` |
| `state.summary` | full aggregate summary (agents + claims + alerts) |
| `state.iter_claims` | end-to-end scan of the claim log |
| `keys.new` | Ed25519 keypair generation (skipped if crypto missing) |
| `health.state_checks` | 6 state-level checks |
| `health.repo_checks` | 7 repo-level checks |
| `health.host_checks` | full host kernel check battery |

### ai

| bench | what it measures |
|---|---|
| `router.cold` | first parallel call (no warm-up) |
| `router.warm` | parallel call after warm-up |
| `model.cold` | single model first call |
| `model.warm` | single model after warm-up |
| `own_models.warm` | the 3 own models (rollout-/repo-aware/spec-citation) |

## output format

JSON: list of `BenchResult` dicts with `name`, `kind`, `iterations`,
`mean_ms`, `median_ms`, `min_ms`, `max_ms`, `stdev_ms`, `notes`.

Markdown: a table suitable for committing to `results/snapshot.md`
so the trend is visible in git history.

## committed snapshots

`results/snapshot.json` and `results/snapshot.md` are the canonical
local snapshots. They are written each time the benchmarks run with
`--output` / `--markdown` flags. The CI workflow re-runs benchmarks
on every push and posts the latest snapshot to the workflow artifacts.
