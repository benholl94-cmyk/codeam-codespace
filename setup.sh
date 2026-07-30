#!/usr/bin/env bash
# rollout-shield installer.
#
# Sets up the runtime on a fresh checkout:
#   1. Verifies Python 3.8+ is available
#   2. Ensures `cryptography` is importable; if not, installs via pip with
#      a tiered fallback chain (--user → --user --break-system-packages →
#      --break-system-packages). Skips pip entirely when the module is
#      already importable — fixes the Debian/PEP 668 hard-fail path.
#   3. Installs the package in editable / sys.path-friendly mode
#   4. Runs `rollout-shield install` to create state dirs + default key
#   5. Prints next steps
#
# Usage:
#   ./setup.sh                # full setup
#   ./setup.sh --no-deps      # skip pip install (use system packages)
#   ./setup.sh --state-root DIR  # override state root
#   ./setup.sh --python PY    # override python interpreter

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
      sed -n '2,22p' "$0"
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

# 2. cryptography ensure — probe first; only invoke pip when truly missing.
#    PEP 668 / Debian-packaged Python refuses `pip install --user` even when
#    cryptography is already importable, so a naïve "always pip install"
#    hard-fails fresh checkout on those systems.
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  echo ">> ensuring runtime dependency: cryptography"
  if "$PYTHON" -c "import cryptography, sys; sys.exit(0 if getattr(cryptography, '__version__', None) else 1)" 2>/dev/null; then
    CRYPTO_VER="$("$PYTHON" -c "import cryptography; print(cryptography.__version__)")"
    echo "   already importable (version: ${CRYPTO_VER}); skipping pip install"
  else
    INSTALLED=0
    for PIP_ARGS in "--user" "--user --break-system-packages" "--break-system-packages"; do
      echo "   attempting: $PYTHON -m pip install --quiet ${PIP_ARGS} cryptography"
      if "$PYTHON" -m pip install --quiet ${PIP_ARGS} cryptography 2>/dev/null; then
        INSTALLED=1
        break
      fi
    done
    if [[ "$INSTALLED" -ne 1 ]]; then
      echo "FAIL: could not install cryptography via pip." >&2
      echo "      options:" >&2
      echo "        - Debian/Ubuntu: sudo apt-get install -y python3-cryptography" >&2
      echo "        - venv:          $PYTHON -m venv .venv && .venv/bin/pip install cryptography" >&2
      echo "        - pipx:          pipx install cryptography" >&2
      echo "        - force:         $PYTHON -m pip install --break-system-packages cryptography" >&2
      exit 1
    fi
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
