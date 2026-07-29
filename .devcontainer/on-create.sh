#!/usr/bin/env bash
# onCreateCommand: per-workspace setup on the prebaked image. Values are provided
# by the platform at create time as environment/secrets (never committed).
#
# Standalone-canonical mode (default):
#   - REPO_URL unset → no clone. Workspace stays as the dev container itself.
#   - REPO_URL set → clone the given URL into WORK. Use a personal-fork URL
#     (e.g., git@github.com:<you>/codeam-codespace.git) when the codespace's
#     authenticated user lacks push rights on the upstream repo. See
#     scripts/setup-fork.sh for an idempotent helper that aligns git origin
#     and .beads/config.yaml sync.remote.
set -uo pipefail
log() { echo "[on-create] $*"; }

WORK="${WORKSPACE_DIR:-/workspaces/repo}"

if [ -n "${REPO_URL:-}" ]; then
  log "cloning user repo → ${WORK}"
  # Deliver the token via a credential helper reading an env var — never put it in
  # the clone URL or argv (leaks via ps/shell history). Placeholder helper:
  git -c credential.helper='!f() { echo "username=x-access-token"; echo "password=${GIT_TOKEN}"; }; f' \
      clone "${REPO_URL}" "${WORK}" || log "clone failed (non-fatal)"
else
  log "no REPO_URL set — scaffold/dev run, skipping clone"
fi

# TODO (backend integration): pair the session using CODEAM_AUTO_PAIR_TOKEN pointed at
# ${WORK}, with the agent auth / house-proxy env the backend delivered — mirrors the
# codespace bootstrap's pair-auto step (see github-ssh.service.ts). Roughly:
#   cd "${WORK}" && CODEAM_AUTO_TOKEN="${CODEAM_AUTO_PAIR_TOKEN}" codeam pair-auto
log "prebaked stack present ($(codeam --version 2>/dev/null || echo 'codeam ?')); pairing wiring is the backend follow-up"
