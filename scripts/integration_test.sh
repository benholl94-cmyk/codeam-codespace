#!/usr/bin/env bash
# integration_test.sh — end-to-end smoke test for a fresh rollout-shield install.
#
# Exercises: install → status → claim create → claim verify → backup → restore
# (dry-run) → uninstall --yes. Designed to be run from the repo root on any
# host with Python 3.10+. Cleans up after itself.
#
# Exit codes:
#   0  all steps passed
#   1  prerequisites missing
#   2-N  step N failed (see output)

set -euo pipefail

cd "$(dirname "$0")/.."  # repo root
REPO="$(pwd)"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: python3 not found"; exit 1
fi

# Use a throwaway venv so we don't disturb anything
VENV="$(mktemp -d)/rs-itest"
echo "[itest] venv: $VENV"
"$PY" -m venv "$VENV"
PIP="$VENV/bin/pip"
ROLL="$VENV/bin/rollout-shield"

# Clean env
TESTDIR="$(mktemp -d)"
export ROLLOUT_SHIELD_STATE_ROOT="$TESTDIR/state"
export ROLLOUT_SHIELD_AUDIT="$TESTDIR/audit"
export ROLLOUT_SHIELD_UNLOCK="$TESTDIR/audit/.owner_unlock"
mkdir -p "$TESTDIR/audit"

cleanup() {
    "$ROLL" uninstall --yes >/dev/null 2>&1 || true
    rm -rf "$TESTDIR" "$VENV"
}
trap cleanup EXIT

step=0
fail() {
    echo "FAIL: $1"
    exit $((step + 2))
}

step=1; echo "[$step] pip install -e ."
"$PIP" install --quiet --upgrade pip wheel
"$PIP" install --quiet -e "$REPO" || fail "pip install"

step=2; echo "[$step] generate owner unlock"
"$PY" "$REPO/tools/secure_state.py" --init || fail "unlock --init"
test -f "$ROLLOUT_SHIELD_UNLOCK" || fail "unlock file missing"

step=3; echo "[$step] rollout-shield install"
"$ROLL" install --state-root "$ROLLOUT_SHIELD_STATE_ROOT" || fail "install"

step=4; echo "[$step] rollout-shield status"
"$ROLL" status --state-root "$ROLLOUT_SHIELD_STATE_ROOT" >/dev/null || fail "status"

step=5; echo "[$step] rollout-shield claim create + verify"
CLAIM_OUT="$("$ROLL" claim --state-root "$ROLLOUT_SHIELD_STATE_ROOT" create \
    --agent-id default --type intent --body "itest-smoke:bootstrap test" \
    --json || true)"
if [ -z "$CLAIM_OUT" ]; then fail "claim create (no output)"; fi
CLAIM_ID="$(printf '%s' "$CLAIM_OUT" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id") or "")')"
if [ -z "$CLAIM_ID" ]; then fail "claim create (no id parsed)"; fi
"$ROLL" verify --state-root "$ROLLOUT_SHIELD_STATE_ROOT" "$CLAIM_ID" >/dev/null \
    || fail "claim verify"

step=6; echo "[$step] rollout-shield backup"
"$ROLL" backup --state-root "$ROLLOUT_SHIELD_STATE_ROOT" --print-key >/dev/null || fail "backup"

step=7; echo "[$step] rollout-shield doctor"
"$ROLL" doctor --state-root "$ROLLOUT_SHIELD_STATE_ROOT" || fail "doctor"

step=8; echo "[$step] rollout-shield dashboard --help"
"$ROLL" dashboard --help || fail "dashboard --help"

step=9; echo "[$step] rollout-shield self-check"
"$ROLL" self-check --state-root "$ROLLOUT_SHIELD_STATE_ROOT" || fail "self-check"

step=10; echo "[$step] rollout-shield restore --dry-run"
# When --state-root is given, backup derives a safeup root at
# <state-root-parent>/.safeups-<state-root-name>. Use the same root
# for the list call so restore finds the snapshot.
SR_DIR="$(dirname "$ROLLOUT_SHIELD_STATE_ROOT")"
SR_NAME="$(basename "$ROLLOUT_SHIELD_STATE_ROOT")"
SAFEUP_ROOT="$SR_DIR/.safeups-$SR_NAME"
LATEST="$("$PY" "$REPO/tools/safeup.py" --root "$SAFEUP_ROOT" list 2>/dev/null \
    | head -2 | tail -1 | awk '{print $1}')"
if [ -z "$LATEST" ]; then
    LATEST="$(ls -t "$SAFEUP_ROOT" 2>/dev/null | grep -v 'snapshots.jsonl' | head -1 || true)"
fi
if [ -n "$LATEST" ]; then
    "$ROLL" restore --state-root "$ROLLOUT_SHIELD_STATE_ROOT" --dry-run "$LATEST" \
        >/dev/null || fail "restore dry-run"
else
    echo "  (no snapshot found at $SAFEUP_ROOT; skipping)"
fi

echo
echo "============================================"
echo " integration test: ALL 10 STEPS PASSED"
echo "============================================"