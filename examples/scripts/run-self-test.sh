#!/usr/bin/env bash
# One-shot smoke test against the live state.
#
# Exits 0 when self-test passes; non-zero otherwise.

set -euo pipefail

STATE_ROOT="${ROLLOUT_SHIELD_STATE:-$HOME/.rollout-shield}"
CLI="${ROLLOUT_SHIELD_CLI:-$(command -v rollout-shield || echo "$HOME/usr/bin/rollout-shield")}"

if [[ ! -x "$CLI" ]]; then
  echo "rollout-shield CLI not found at $CLI" >&2
  echo "  install with: scripts/install.sh" >&2
  exit 1
fi

echo "running self-test against $STATE_ROOT ..."
"$CLI" --state-root "$STATE_ROOT" self-test
