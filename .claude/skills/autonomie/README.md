# autonomie — Full-Cycle Autonomous Build Chain

A permbound Claude Code skill bundle for `benholl94-cmyk/codeam-codespace`
that runs a 5-stage chain (plan → build → verify → commit → monitor) with
safeup-first data protection, beads-tracked tasks, and failuresafe recovery.

## Contents

```
.claude/skills/autonomie/
├── SKILL.md              # main skill entry (auto-loads on trigger phrases)
├── README.md             # this file
└── scripts/
    ├── preflight.sh      # gate check before any chain run
    ├── autonomy.sh       # the 5-stage orchestrator
    ├── chain_status.sh   # show current chain health
    └── install_hook.sh   # permbind as SessionStart hook
```

## Trigger Phrases

The skill auto-loads when the user says any of:
- "build full autonomously"
- "autonomie" / "autonom"
- "you have full-repo-restriction-free"
- "owner_repo"
- "no manuel task input"
- "100% smart highquality"
- "safeups for Data-loose"
- "creative workarounds" / "rotating safe-data-sets with rollbacks"

## Install / Verify

```bash
# permbind so the skill auto-loads every session (one-time)
.claude/skills/autonomie/scripts/install_hook.sh

# verify the chain works end-to-end (dry-run)
.claude/skills/autonomie/scripts/autonomy.sh \
    --intent "smoke" --request "validate the bundle" \
    --skip-tests --dry-run

# check chain health any time
.claude/skills/autonomie/scripts/chain_status.sh
```

## The 5-Stage Chain

| Stage | What runs | Gate |
|-------|-----------|------|
| 1 PLAN | `bd ready` + scope task via `bd create` | each task has a bead |
| 2 BUILD | `tools/safeup.py snapshot --op <op>` then run user cmd | safeup succeeds |
| 3 VERIFY | `tests/run_all.py` + `tools/doctor.py` + `tools/safeup.py verify` | all green |
| 4 COMMIT | `bd close` + `git add -A` + `git commit` (push only with `--push`) | git tree clean |
| 5 MONITOR | final `doctor.py` + handoff printout | doctor = 0 fail |

## Failuresafes

See SKILL.md for the full recovery matrix. Highlights:

- **Tests fail** → fix root cause, never disable
- **Safeup corrupt** → `tools/safeup.py restore <last-good-id>`
- **Git conflict** → rebase; on failure, safeup rollback
- **Tool unavailable** → fall back to grep/awk/python (stdlib-only)
- **Network outage** → continue offline; defer pushes
- **Rate limit** → exponential backoff 2s/4s/8s, then queue
- **Dependency missing** → probe-then-tiered-install (see setup.sh)
- **Permission denied** → surface exact error + ask operator
- **Disk full** → `safeup.py prune --keep 5`; refuse new writes
- **Mid-write kill** → atomic writes in `state.py` guarantee safety
- **Hook blocks commit** → read + fix; never `--no-verify`

## Guarantees

1. **Safeup-first** — every BUILD starts with a snapshot
2. **Stdlib-only** — no runtime pip install
3. **Beads-tracked** — every meaningful task has an open bead before code
4. **Failuresafe-recoverable** — each stage has a recovery procedure
5. **Operator-visible** — every action is echoed
6. **Reversible** — `safeup restore` rewinds in 10 seconds

## Related Infra (already in this repo)

- `tools/safeup.py` — snapshot/rotate/restore + preop auto-rollback
- `tools/doctor.py` — 10-check health probe
- `tools/release.py` — semver bump → CHANGELOG → tag pipeline
- `setup.sh` — probe-then-tiered-install for `cryptography`
- `tests/run_all.py` — 30 tests across 7 modules
- `rollout_shield/state.py` — atomic writes + write-lock + migration registry
