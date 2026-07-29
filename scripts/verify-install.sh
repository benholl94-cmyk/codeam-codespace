#!/usr/bin/env bash
# Verify a hard-built rollout-shield install at ~/usr/.
#
# Checks:
#   - prefix structure exists
#   - bin shims are executable
#   - package copy exists and is importable
#   - interface assets are present
#   - CLI runs (--version, --help)
#   - state root is healthy (separate from prefix)
#
# Exit codes:
#   0 — all checks pass
#   1 — one or more checks failed (see stderr)

set -euo pipefail

PREFIX="${HOME}/usr"
STATE_ROOT="${HOME}/.rollout-shield"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)     PREFIX="$2"; shift 2 ;;
    --state-root) STATE_ROOT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

PASS=0
FAIL=0
check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '  [OK]   %s\n' "$label"
    PASS=$((PASS + 1))
  else
    printf '  [FAIL] %s\n' "$label"
    FAIL=$((FAIL + 1))
  fi
}

echo "verifying install at $PREFIX"

# --- structural checks ---
check "prefix exists"              test -d "$PREFIX"
check "bin/ exists"                test -d "$PREFIX/bin"
check "lib/python/ exists"         test -d "$PREFIX/lib/python"
check "share/rollout-shield exists" test -d "$PREFIX/share/rollout-shield"
check "etc/rollout-shield exists"  test -d "$PREFIX/etc/rollout-shield"
check "INSTALLED_AT marker"        test -f "$PREFIX/INSTALLED_AT"
check "REPO_SOURCE marker"         test -f "$PREFIX/REPO_SOURCE"

# --- shim executables ---
check "bin/rollout-shield executable"     test -x "$PREFIX/bin/rollout-shield"
check "bin/rollout-shield-monitor executable" test -x "$PREFIX/bin/rollout-shield-monitor"

# --- package copy ---
check "package copy exists"      test -d "$PREFIX/lib/python/rollout_shield"
check "package init.py exists"   test -f "$PREFIX/lib/python/rollout_shield/__init__.py"
check "package cli.py exists"    test -f "$PREFIX/lib/python/rollout_shield/cli.py"

# --- interface assets ---
check "interface/index.html exists" test -f "$PREFIX/share/rollout-shield/interface/index.html"
check "interface/app.js exists"      test -f "$PREFIX/share/rollout-shield/interface/app.js"
check "interface/style.css exists"   test -f "$PREFIX/share/rollout-shield/interface/style.css"

# --- CLI runtime checks ---
check "rollout-shield --version" bash -c "\"$PREFIX/bin/rollout-shield\" --version"
check "rollout-shield --help"    bash -c "\"$PREFIX/bin/rollout-shield\" --help | head -3"
check "rollout-shield status --json" bash -c "\"$PREFIX/bin/rollout-shield\" status --json --state-root \"$STATE_ROOT\""

# --- PATH check (warning, not fail) ---
if [[ ":$PATH:" == *":$PREFIX/bin:"* ]]; then
  echo "  [OK]   $PREFIX/bin on PATH"
else
  echo "  [WARN] $PREFIX/bin not on PATH (CLI still callable via absolute path)"
fi

echo
echo "passed: $PASS"
echo "failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
