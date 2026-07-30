#!/usr/bin/env bash
# preflight.sh — gate check before any autonomie chain run.
# Returns 0 if all gates pass; non-zero with reason otherwise.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

fail() { printf '  [FAIL] %s\n' "$*" >&2; exit 1; }
ok()   { printf '  [OK]   %s\n' "$*"; }

echo "[autonomie] preflight @ $REPO_ROOT"

# 1. python present and >= 3.8
command -v python3 >/dev/null || fail "python3 not in PATH"
PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
MAJOR="$(echo "$PY_VER" | cut -d. -f1)"; MINOR="$(echo "$PY_VER" | cut -d. -f2)"
if [[ "$MAJOR" -lt 3 || ("$MAJOR" -eq 3 && "$MINOR" -lt 8) ]]; then
    fail "python $PY_VER < 3.8"
fi
ok "python $PY_VER"

# 2. safeup available
[[ -x "$REPO_ROOT/tools/safeup.py" ]] || fail "tools/safeup.py missing"
ok "safeup.py present"

# 3. doctor available
[[ -x "$REPO_ROOT/tools/doctor.py" ]] || fail "tools/doctor.py missing"
ok "doctor.py present"

# 4. tests runnable
[[ -f "$REPO_ROOT/tests/run_all.py" ]] || fail "tests/run_all.py missing"
ok "tests/run_all.py present"

# 5. bd (beads) CLI present (preferred; fall back to no-beads mode)
if command -v bd >/dev/null 2>&1; then
    ok "bd available: $(bd --version 2>/dev/null || echo unknown)"
else
    echo "  [WARN] bd not in PATH — chain will skip beads-tracked tasks"
fi

# 6. safeup index writable
mkdir -p "$REPO_ROOT/.safeups" || fail "cannot create .safeups/"
ok "safeup root writable: .safeups/"

# 7. .beads dir (optional)
if [[ -d "$REPO_ROOT/.beads" ]]; then
    ok ".beads/ present"
else
    echo "  [WARN] .beads/ absent — running standalone (no beads tracking)"
fi

# 8. git on a branch with a commit
if [[ -d "$REPO_ROOT/.git" ]]; then
    HEAD_REF="$(git symbolic-ref --short HEAD 2>/dev/null || echo detached)"
    HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
    ok "git: branch=$HEAD_REF head=$HEAD_SHA"
else
    echo "  [WARN] no .git/ — chain still runs but commit stage is skipped"
fi

# 9. doctor.py status
if python3 "$REPO_ROOT/tools/doctor.py" >/dev/null 2>&1; then
    ok "doctor.py: 0 fails"
else
    fail "doctor.py reports failures — fix before running autonomie"
fi

echo "[autonomie] preflight OK"
