# scripts/

Workspace-local scripts for standalone-canonical mode. These run on the dev container, not in CI.

## bootstrap.sh

One-command installer for Termux/Linux/macOS. Verifies Python 3.10+,
creates `./.venv`, pip-installs the package editable, generates an owner
unlock (`.audit/.owner_unlock`, mode 0600) if missing, prints the
32-word paper backup phrase to `/tmp/rollout-shield-phrase.txt`, and
runs `rollout-shield doctor` for a final health check.

```bash
bash scripts/bootstrap.sh
# Idempotent — safe to re-run; existing .venv and unlock are reused.
```

Companion to the repo-root `setup.sh`: `bootstrap.sh` uses a venv and
auto-generates the owner-unlock; `setup.sh` uses system Python (with
a `cryptography` ensure-via-pip fallback chain) and delegates state
creation to `rollout-shield install`.

## integration_test.sh

End-to-end smoke test for a fresh install: install → status → claim
create → claim verify → backup → doctor → dashboard help → self-check
→ restore dry-run. Designed for CI and one-shot regression checks.

```bash
PYTHON=python3 bash scripts/integration_test.sh
# Exit 0 = all 10 steps passed. Uses a throwaway venv and tempdir.
```

## export-state.sh

Bundle the entire repo state into a portable `.tgz` archive.

```bash
./scripts/export-state.sh
# Produces dist/codeam-codespace-<sha>-<timestamp>.tgz containing:
#   - state.bundle          (git bundle, full history of all refs)
#   - issues.jsonl          (passive JSONL export of beads issues)
#   - beads-config.yaml     (snapshot of .beads/config.yaml)
#   - beads-metadata.json   (snapshot of .beads/metadata.json)
#   - EXPORT-MANIFEST.txt   (timestamp, branch, commit, bd stats, restore steps)
```

**Restore on another machine:**
```bash
tar xzf codeam-codespace-<sha>-<timestamp>.tgz
mkdir restore && cd restore
git clone ../state.bundle -b main .
cp ../beads-config.yaml ../beads-metadata.json .beads/
bd import < ../issues.jsonl
bd doctor --check=conventions
```

## setup-fork.sh

Idempotently point the workspace at a personal fork so `git push fork main` and `bd dolt push` work.

```bash
FORK_OWNER=my-github-user ./scripts/setup-fork.sh
# Re-runs with the same FORK_OWNER are a no-op.
# Re-run with FORCE=true to overwrite an existing fork remote.
```

**Effects:**
- Adds `fork` remote at `git@github.com:${FORK_OWNER}/codeam-codespace.git`.
- Sets `.beads/config.yaml` `sync.remote` to match.

**To revert** to standalone (no remote) mode:
```bash
git remote remove fork
# Edit .beads/config.yaml: set sync.remote to "" (or re-comment).
```

## Round-trip property

The two scripts compose: `setup-fork.sh` configures the publish path; `export-state.sh` provides the offline backup path. Either is sufficient on its own — they are independent tools.

## Why these exist

If the codespace's authenticated user lacks push rights on the upstream org (a common case for template repos), these scripts provide two non-conflicting alternatives:
1. **Personal-fork publish** — when the user controls a fork.
2. **Offline bundle** — when the user has no push destination, but can transport files.
