#!/usr/bin/env bash
# privacy_audit.sh — full zero-leak audit in one command.
#
# Runs (in order):
#   1. tools/leak_check.sh      — repo static scan
#   2. tools/owner_log.py verify — audit-log hash chain
#   3. tools/secure_state.py --check — encryption state
#   4. .gitignore coverage      — last-mile check
#   5. doctor.py                — full health probe
#
# Exits 0 only if every check passes.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

ok()   { printf '  [OK]   %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }

FAIL=0
echo "=========================================="
echo " privacy_audit — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================="

echo
echo "[1/5] leak_check.sh …"
if tools/leak_check.sh >/dev/null 2>&1; then
    ok "leak_check passed"
else
    fail "leak_check reported leaks — run with verbose output"
fi

echo
echo "[2/5] audit log chain …"
if python3 tools/owner_log.py verify >/dev/null 2>&1; then
    ok "audit chain clean"
else
    fail "audit chain tampered or empty"
fi

echo
echo "[3/5] secure-state unlock …"
if python3 tools/secure_state.py --check >/dev/null 2>&1; then
    ok "owner unlock present"
else
    echo "  [WARN] owner unlock absent — encrypted state will refuse ops"
fi

echo
echo "[4/5] gitignore hardening …"
MISSING=0
for pat in '.audit/' '.env' '*.pem' '*.key' '.rollout-shield/' '.safeups/'; do
    if ! grep -qE "^${pat//./\\.}" .gitignore 2>/dev/null; then
        echo "  [FAIL] gitignore missing: $pat"
        MISSING=1
    fi
done
[[ $MISSING -eq 0 ]] && ok "all sensitive patterns gitignored" || FAIL=1

echo
echo "[5/5] doctor.py …"
if python3 tools/doctor.py >/dev/null 2>&1; then
    ok "doctor reports 0 failures"
else
    fail "doctor reported failures"
fi

echo
echo "=========================================="
if [[ $FAIL -eq 0 ]]; then
    echo " PRIVACY AUDIT: PASS"
else
    echo " PRIVACY AUDIT: FAIL — review above"
fi
echo "=========================================="

exit $FAIL
