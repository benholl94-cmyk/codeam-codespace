#!/usr/bin/env bash
# chain_status.sh — show current autonomie chain health + recent activity.
set -uo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "=========================================="
echo " autonomie chain status"
echo "=========================================="

# 1. safeups
echo
echo "[safeups]"
if [[ -d .safeups ]]; then
    COUNT="$(ls -1 .safeups 2>/dev/null | grep -v snapshots.jsonl | grep -v restore-last | wc -l)"
    echo "  snapshots retained: $COUNT"
    python3 tools/safeup.py list 2>/dev/null | head -5 || true
else
    echo "  no .safeups/ yet"
fi

# 2. beads
echo
echo "[beads]"
if command -v bd >/dev/null 2>&1; then
    OPEN="$(bd list --status=open 2>/dev/null | grep -c '^○\|^◐' || true)"
    echo "  open: $OPEN"
    bd ready 2>/dev/null | head -3 || true
else
    echo "  bd not installed"
fi

# 3. git
echo
echo "[git]"
git log --oneline -3 2>/dev/null
git status --short 2>/dev/null | head -10 || echo "  clean"

# 4. doctor
echo
echo "[doctor]"
python3 tools/doctor.py 2>/dev/null | tail -5 || true

echo
echo "=========================================="
