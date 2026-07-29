#!/usr/bin/env bash
# rollout-shield installer.
#
# Sets up the runtime on a fresh checkout:
#   1. Verifies Python 3.8+ is available
#   2. Verifies pip can install the optional `cryptography` dep
#   3. Installs the package in editable / sys.path-friendly mode
#   4. Runs `rollout-shield install` to create state dirs + default key
#   5. Prints next steps
#
# Usage:
#   ./setup.sh                # full setup
#   ./setup.sh --no-deps      # skip pip install (use system packages)
#   ./setup.sh --state-root DIR  # override state root

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
STATE_ROOT=""
INSTALL_DEPS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-deps) INSTALL_DEPS=0; shift ;;
    --state-root) STATE_ROOT="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo ">> rollout-shield installer"
echo "   python: $($PYTHON --version 2>&1)"
echo "   cwd:    $(pwd)"
echo

# 1. python check
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "FAIL: $PYTHON not in PATH" >&2
  exit 1
fi

# 2. cryptography install
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  echo ">> installing runtime dependency: cryptography"
  if ! "$PYTHON" -m pip install --quiet --user cryptography; then
    echo "FAIL: pip install cryptography failed" >&2
    echo "      try: $PYTHON -m pip install cryptography" >&2
    exit 1
  fi
fi

# 3. ensure bin/ is on PATH for this shell
BIN_DIR="$(pwd)/bin"
echo ">> bin dir: $BIN_DIR"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "   hint: export PATH=\"$BIN_DIR:\$PATH\""
fi

# 4. make rollout-shield executable
chmod +x bin/rollout-shield 2>/dev/null || true

# 5. run install via python module form (more reliable than a fresh shell)
echo ">> running: rollout-shield install"
INSTALL_ARGS=()
if [[ -n "$STATE_ROOT" ]]; then
  INSTALL_ARGS+=(--state-root "$STATE_ROOT")
fi

if "$PYTHON" -m rollout_shield install "${INSTALL_ARGS[@]}"; then
  echo
  echo ">> setup complete"
  echo
  echo "next steps:"
  echo "  export PATH=\"$BIN_DIR:\$PATH\""
  echo "  rollout-shield status"
  echo "  rollout-shield self-check"
  echo "  rollout-shield monitor --once"
  echo "  rollout-shield dashboard --port 8765"
else
  echo "FAIL: rollout-shield install returned non-zero" >&2
  exit 1
fi
