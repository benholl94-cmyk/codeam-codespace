#!/usr/bin/env bash
# export-state.sh — bundle the entire repo state into a portable artifact.
#
# Produces a single .tar.gz containing:
#   - git bundle (full history of all refs)
#   - .beads/issues.jsonl (passive JSONL export)
#   - .beads/config.yaml + .beads/metadata.json (beads config snapshot)
#   - EXPORT-MANIFEST.txt (metadata: timestamp, branch, commit, bd stats)
#
# Use case: standalone-canonical mode where the workspace has no upstream push
# rights. Bundle is round-trippable via:
#   tar xzf state.tgz
#   git clone state.bundle -b main /tmp/restore
#   (cd /tmp/restore && bd import < issues.jsonl)
#
# Designed to be safe to re-run; output filenames include a timestamp.

set -euo pipefail

log() { echo "[export-state] $*"; }

# Resolve repo root (script lives in scripts/).
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

# Collect metadata.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short HEAD)"
BD_STATS="$(bd stats 2>&1 || echo 'bd unavailable')"
ISSUE_COUNT="$(bd list --all 2>/dev/null | wc -l || echo '?')"

# Ensure issues.jsonl is up to date.
if [ ! -f .beads/issues.jsonl ]; then
  log "no .beads/issues.jsonl — running bd export"
  bd export > .beads/issues.jsonl
fi

# Build output paths.
OUT_DIR="${REPO_ROOT}/dist"
mkdir -p "${OUT_DIR}"
BUNDLE_NAME="codeam-codespace-${SHORT_SHA}-${TS}"
BUNDLE_FILE="${OUT_DIR}/${BUNDLE_NAME}.bundle"
ARCHIVE_FILE="${OUT_DIR}/${BUNDLE_NAME}.tgz"

log "exporting state to ${ARCHIVE_FILE}"
log "  branch:  ${BRANCH}"
log "  commit:  ${HEAD_SHA}"
log "  issues:  ${ISSUE_COUNT}"

# 1. git bundle (full history of all refs).
git bundle create "${BUNDLE_FILE}" --all

# 2. Stage auxiliary files in a temp dir.
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
cp "${BUNDLE_FILE}" "${TMP}/state.bundle"
cp .beads/issues.jsonl "${TMP}/issues.jsonl"
cp .beads/config.yaml "${TMP}/beads-config.yaml"
cp .beads/metadata.json "${TMP}/beads-metadata.json"

cat > "${TMP}/EXPORT-MANIFEST.txt" <<EOF
export-state.sh manifest
=======================
timestamp:      ${TS}
branch:         ${BRANCH}
head_commit:    ${HEAD_SHA}
bundle_file:    state.bundle
beads_db:       ${BD_STATS}
issue_count:    ${ISSUE_COUNT}
restore_steps:
  1. mkdir restore && cd restore
  2. git clone ../state.bundle -b main .
  3. cp ../beads-config.yaml ../beads-metadata.json .beads/
  4. bd import < ../issues.jsonl
  5. bd doctor --check=conventions
EOF

# 3. Archive everything.
tar czf "${ARCHIVE_FILE}" -C "${TMP}" .

log "done: ${ARCHIVE_FILE} ($(du -h "${ARCHIVE_FILE}" | cut -f1))"
log "restore on another machine:"
log "  tar xzf ${ARCHIVE_FILE##*/}"
log "  git clone state.bundle -b main restore"
log "  (cd restore && cp ../beads-config.yaml ../beads-metadata.json .beads/ && bd import < ../issues.jsonl)"
