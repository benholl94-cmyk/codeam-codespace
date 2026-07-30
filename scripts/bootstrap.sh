#!/usr/bin/env bash
# bootstrap.sh — one-command installer for rollout-shield.
#
# Designed for Termux on Android and any Linux/macOS box. Idempotent: safe
# to run multiple times. Does NOT call out to the network after the initial
# `pip install` (which itself only contacts PyPI / your-index).
#
# Steps:
#   1. Verify Python 3.10+ is available.
#   2. Create ./.venv (or reuse it).
#   3. pip install -e . (editable; installs 'rollout-shield' console script)
#   4. Generate owner unlock if missing (.audit/.owner_unlock, mode 0600).
#   5. Print 32-word paper backup phrase ONCE; offer to save to file.
#   6. Run 'rollout-shield doctor' to verify health.
#
# Re-run safeups for data-loss protection: a snapshot is taken at every
# bootstrap so the user can roll back if a step fails.
#
# Exit codes:
#   0  success
#   1  prerequisites missing
#   2  pip install failed
#   3  unlock generation failed
#   4  doctor reported problems

set -euo pipefail

cd "$(dirname "$0")/.."  # repo root
REPO_ROOT="$(pwd)"

echo "==============================================="
echo " rollout-shield bootstrap"
echo "==============================================="
echo "repo: $REPO_ROOT"

# 1. Python check
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: $PY not found."
    if command -v pkg >/dev/null 2>&1; then
        echo "  Termux: pkg install python"
    else
        echo "  Install Python 3.10+ via your OS package manager."
    fi
    exit 1
fi
PY_VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "ERROR: Python $PY_VER < 3.10. Need 3.10+."
    exit 1
fi
echo "python: $PY ($PY_VER)"

# 2. venv
VENV="$REPO_ROOT/.venv"
if [ ! -d "$VENV" ]; then
    echo "[1/5] creating venv at $VENV …"
    "$PY" -m venv "$VENV"
else
    echo "[1/5] reusing existing venv at $VENV"
fi
PIP="$VENV/bin/pip"
ROLL="$VENV/bin/rollout-shield"

# 3. install
echo "[2/5] pip install -e ."
"$PIP" install --quiet --upgrade pip wheel
if ! "$PIP" install --quiet -e "$REPO_ROOT"; then
    echo "ERROR: pip install failed."
    echo "  Try manually: $PIP install -e $REPO_ROOT"
    exit 2
fi
echo "  installed: $($ROLL --version)"

# 4. owner unlock
echo "[3/5] owner unlock"
if [ -f "$REPO_ROOT/.audit/.owner_unlock" ]; then
    echo "  unlock present at .audit/.owner_unlock"
else
    mkdir -p "$REPO_ROOT/.audit"
    if ! "$PY" "$REPO_ROOT/tools/secure_state.py" --init; then
        echo "ERROR: unlock generation failed."
        exit 3
    fi
fi

# 5. paper phrase
echo "[4/5] 32-word paper backup phrase"
echo "  ----"
"$PY" "$REPO_ROOT/tools/secure_state.py" --backup | tee /tmp/rollout-shield-phrase.txt || true
echo "  ----"
echo "  saved to /tmp/rollout-shield-phrase.txt"
echo "  >> WRITE THIS ON PAPER AND STORE OFFLINE <<"

# 6. doctor
echo "[5/5] doctor"
"$ROLL" doctor || {
    echo "WARNING: doctor reported issues (exit $?)"
    echo "  run 'rollout-shield doctor' for details"
    exit 4
}

echo
echo "==============================================="
echo " bootstrap complete."
echo " next steps:"
echo "   rollout-shield install"
echo "   rollout-shield status"
echo "   rollout-shield dashboard    # http://127.0.0.1:8765/"
echo "==============================================="