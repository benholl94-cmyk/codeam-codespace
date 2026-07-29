#!/usr/bin/env bash
# setup-fork.sh — idempotently point the workspace at a personal fork.
#
# Use case: when the workspace authenticates as a user without push rights to
# the upstream org (CodeAgentMobile/codeam-codespace), this script swaps the
# git origin to a personal fork where the same user DOES have push rights.
#
# Idempotent: re-running with the same arguments is a no-op (just refreshes
# the bd sync.remote to match).
#
# Arguments (env vars, since flags would conflict with bd env conventions):
#   FORK_OWNER   — GitHub username/org that owns the fork (required)
#   FORK_REPO    — repo name (default: codeam-codespace)
#   FORCE        — if "true", overwrite an existing fork remote
#
# Example:
#   FORK_OWNER=benholl94-cmyk ./scripts/setup-fork.sh
#
# After running, `git push fork main` becomes the canonical publish action.
# `bd dolt push` will then succeed (assuming the fork is writable).

set -euo pipefail

log() { echo "[setup-fork] $*"; }
err() { echo "[setup-fork] ERROR: $*" >&2; exit 1; }

: "${FORK_OWNER:?FORK_OWNER must be set (e.g., export FORK_OWNER=my-github-user)}"
FORK_REPO="${FORK_REPO:-codeam-codespace}"
FORCE="${FORCE:-false}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

FORK_URL="git@github.com:${FORK_OWNER}/${FORK_REPO}.git"
HTTPS_URL="https://github.com/${FORK_OWNER}/${FORK_REPO}.git"

# Check whether 'fork' remote already exists.
if git remote get-url fork >/dev/null 2>&1; then
  EXISTING="$(git remote get-url fork)"
  if [ "${EXISTING}" = "${FORK_URL}" ] || [ "${EXISTING}" = "${HTTPS_URL}" ]; then
    log "fork remote already points at ${EXISTING} — no change"
  else
    if [ "${FORCE}" != "true" ]; then
      err "fork remote already exists pointing at '${EXISTING}'. Re-run with FORCE=true to overwrite, or update manually: git remote set-url fork <url>"
    fi
    log "updating fork remote from ${EXISTING} to ${FORK_URL}"
    git remote set-url fork "${FORK_URL}"
  fi
else
  log "adding fork remote: ${FORK_URL}"
  git remote add fork "${FORK_URL}"
fi

# Update bd sync.remote to match.
log "updating .beads/config.yaml sync.remote to ${FORK_URL}"
# Use a Python one-liner for safe YAML-ish edit (no external YAML dep).
python3 - <<PY
import re, sys
path = ".beads/config.yaml"
with open(path) as f:
    content = f.read()
# Match either commented-out sync.remote or active one.
pattern = re.compile(r'^(#\s*)?sync\.remote:\s*".*?"\s*$', re.MULTILINE)
replacement = f'sync.remote: "{sys.argv[1]}"'
new_content, n = pattern.subn(replacement, content, count=1)
if n == 0:
    # No existing line — append before 'dolt:' section.
    new_content = re.sub(
        r'^(dolt:)',
        f'sync.remote: "{sys.argv[1]}"\n\\1',
        content,
        count=1,
        flags=re.MULTILINE,
    )
with open(path, "w") as f:
    f.write(new_content)
PY

# Verify
log "verifying..."
git remote -v
bd dolt config get sync.remote 2>&1 || log "  (bd dolt config not available; verify .beads/config.yaml directly)"

log "next steps:"
log "  git push fork main"
log "  bd dolt push"
log ""
log "to revert to standalone (no remote) mode:"
log "  git remote remove fork"
log "  edit .beads/config.yaml: set sync.remote to '' (or re-comment)"
