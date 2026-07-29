#!/usr/bin/env bash
# Uninstall rollout-shield from the user-local prefix.
#
# Usage:
#   scripts/uninstall.sh             # remove ~/usr/ (default)
#   scripts/uninstall.sh --prefix /tmp/x
#   scripts/uninstall.sh --keep-state # leave ~/.rollout-shield/ alone (default)
#
# By default removes ONLY the install prefix. Runtime state at
# ~/.rollout-shield/ is preserved — pass --purge-state to also remove it.

set -euo pipefail

PREFIX="${HOME}/usr"
PURGE_STATE=0
KEEP_STATE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)        PREFIX="$2"; shift 2 ;;
    --purge-state)   PURGE_STATE=1; KEEP_STATE=0; shift ;;
    --keep-state)    KEEP_STATE=1; shift ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '[uninstall] %s\n' "$*" >&2; }

if [[ ! -d "$PREFIX" ]]; then
  log "$PREFIX does not exist — nothing to remove"
  exit 0
fi

# Refuse to rm something we did not install. The marker file is set by install.sh.
if [[ ! -f "$PREFIX/INSTALLED_AT" ]]; then
  printf '[uninstall] REFUSE: %s lacks INSTALLED_AT marker — was it installed via scripts/install.sh?\n' "$PREFIX" >&2
  exit 1
fi

log "removing $PREFIX"
rm -rf "$PREFIX"

if [[ "$PURGE_STATE" -eq 1 ]]; then
  STATE_DIR="${HOME}/.rollout-shield"
  if [[ -d "$STATE_DIR" ]]; then
    log "purging state at $STATE_DIR"
    rm -rf "$STATE_DIR"
  else
    log "no state to purge at $STATE_DIR"
  fi
else
  log "state at ~/.rollout-shield/ preserved (pass --purge-state to remove)"
fi

log "OK — uninstall complete"
