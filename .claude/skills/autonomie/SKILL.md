---
name: autonomie
description: Full-cycled autonomous build chain for this repo (owner_repo = benholl94-cmyk/codeam-codespace). Auto-loads on user phrases like "build full autonomously", "autonomie", "you have full-repo-restriction-free", "owner_repo", "no manuel task input", "100% smart highquality", "creative workarounds with safeups". Chains plan -> build -> verify -> commit -> monitor with safeup-first data protection, beads-tracked tasks, failuresafe recovery, and offline-first operation. Use when the user grants broad build autonomy and wants the cycle to run end-to-end without per-step approval.
metadata:
  owner_repo: benholl94-cmyk/codeam-codespace
  chain: plan,build,verify,commit,monitor
  guarantees: [safeup-first, stdlib-only, beads-tracked, failuresafe-recoverable]
---

# Autonomie — Full-Cycle Autonomous Build Chain

When the owner grants full-repo autonomy, this skill enforces a non-skippable
chain that runs to completion. Each stage has a failuresafe. No step runs
unless the prior step's gate passes.

## Trigger Phrases (auto-load)

Any of these in the user message loads this skill:

- "build full autonomously"
- "autonomie" / "autonom"
- "you have full-repo-restriction-free"
- "owner_repo" / "owner repo"
- "no manuel task input" / "no manuel task"
- "I let you Build Full autonomously"
- "100% smart highquality" / "highquality"
- "safeups for Data-loose" / "safeup first"
- "creative workarounds" / "rotating safe-data-sets with rollbacks"

## The Chain (5 stages, no skipping)

```
   +---------+   +--------+   +---------+   +--------+   +---------+
   | 1.PLAN  |-->|2.BUILD |-->|3.VERIFY |-->|4.COMMIT|-->|5.MONITOR|
   +---------+   +--------+   +---------+   +--------+   +---------+
       |             |             |             |             |
      bd           safeup        tests         bd close    doctor.py
      ready       snapshot       + doctor      + git add    + watch
      + scope     + write        + safeup      + commit    + bead
```

### Stage 1 — PLAN
- Run `bd ready` to surface unblocked work
- Break the user's request into concrete tasks
- For each task: `bd create --type=task --priority=2` BEFORE writing code
- Identify the **safeup op name** (kebab-case slug of intent)
- Identify files likely to change (`git status --porcelain` baseline)
- **Gate:** every task has a bead, op name is set.

### Stage 2 — BUILD
- **First action: safeup snapshot** (`tools/safeup.py snapshot --op <op>`)
- Make edits; match existing code style (comment density, naming, idiom)
- For risky/external ops: use `tools/safeup.py preop --op <op> -- <cmd>`
  which auto-rolls-back on non-zero exit
- Never commit code that breaks a test. If a test breaks, fix it before moving on
- **Gate:** `python3 tests/run_all.py` returns 0.

### Stage 3 — VERIFY
- Run `python3 tools/doctor.py` — must show 0 fail
- Run `python3 tools/safeup.py verify` — must show 0 corrupt
- Run full test suite again (catches flakes): `python3 tests/run_all.py`
- Spot-check imports: `python3 -W error -c "import rollout_shield"`
- **Gate:** doctor = 0 fail, safeup verify = 0 corrupt, tests = 30+ pass.

### Stage 4 — COMMIT
- `bd close <id1> <id2> ...` for completed tasks
- `git add` only files you actually changed (no `git add .` unless safe)
- Commit message: imperative mood, Co-Authored-By trailer
- Push **only when the user explicitly authorizes** (conservative profile)
- **Gate:** `git status` clean or only the beads auto-export dirty.

### Stage 5 — MONITOR
- Re-run `doctor.py` post-push to confirm no regressions
- Watch for any deferred/untracked work; create beads for follow-up
- Print a clean handoff: changed files, validation, suggested next commands
- **Gate:** handoff printed, no FAILs in doctor.

## Failuresafes (recovery procedures)

When a stage's gate fails, fall through to the matching recovery. **Never
silently retry more than 2x without operator input.**

| Failure | Recovery |
|---|---|
| Tests fail | Read the failure trace; fix root cause; rerun; never disable tests |
| Doctor fails | Address each ✗ line item; rerun until clean |
| Safeup corrupt | `tools/safeup.py restore <last-good-id>` after a pre-snapshot |
| Git conflict | `git fetch && git rebase origin/main`; on failure, safeup rollback |
| Tool unavailable (e.g. jq) | Use grep/awk/sed/python equivalent (stdlib-only always) |
| Network outage | Continue offline; defer pushes; surface "offline" status |
| Rate limit | Exponential backoff 2s, 4s, 8s, then queue + surface to operator |
| Dependency missing | Probe-then-tiered-install pattern (see setup.sh); never hard-fail |
| Permission denied | Surface exact command + chmod/perms error; ask operator |
| Disk full | Purge oldest safeup (`safeup.py prune --keep 5`); refuse writes |
| Process killed mid-write | Atomic writes in state.py guarantee partial-write safety |
| Hook blocks commit | Read hook output verbatim; fix cause; never `--no-verify` |
| Beads sync conflict | `bd doctor`; if Dolt remote is misconfigured, work locally |

## CLI Entry Point

```bash
.claude/skills/autonomie/scripts/autonomy.sh \
    --intent "<short slug>" \
    --request "<user's verbatim request>"
```

The script runs the chain stages with gate enforcement and prints a final
manifest. See `scripts/autonomy.sh` for the implementation.

## Owner-Repo Binding

This skill auto-loads for the owner_repo `benholl94-cmyk/codeam-codespace`
(verified by `git remote get-url origin`). For other repos, the skill still
loads on the trigger phrases above but safeup/beads paths must be present.

## Guarantees (the contract)

1. **Safeup-first**: every BUILD starts with a snapshot; rollback is one command
2. **Stdlib-only**: no pip install at runtime; only `cryptography` for crypto
3. **Beads-tracked**: every meaningful task has an open bead before code lands
4. **Failuresafe-recoverable**: each stage has a documented recovery procedure
5. **Operator-visible**: every action is echoed; nothing happens silently
6. **Reversible**: a `safeup restore` rewinds to any snapshot in 10 seconds

## Related Infra (already present in this repo)

- `tools/safeup.py` — snapshot/rotate/restore + preop auto-rollback
- `tools/doctor.py` — 10-check health probe with JSON output
- `tools/release.py` — safeup → tests → semver bump → tag pipeline
- `setup.sh` — probe-then-tiered-install for `cryptography`
- `tests/run_all.py` — 30 tests across 7 modules
- `rollout_shield/state.py` — atomic writes, write-lock, migration registry
