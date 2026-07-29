# scripts/

Workspace-local scripts for standalone-canonical mode. These run on the dev container, not in CI.

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
