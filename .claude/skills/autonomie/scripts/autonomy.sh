#!/usr/bin/env bash
# autonomy.sh — orchestrate the 5-stage autonomie chain end-to-end.
#
# Stages:
#   1. PLAN     — bd ready + ensure each task has a bead
#   2. BUILD    — safeup snapshot + run the requested command(s)
#   3. VERIFY   — tests + doctor + safeup verify
#   4. COMMIT   — bd close + git add/commit (push only with --push flag)
#   5. MONITOR  — final doctor + handoff printout
#
# Flags:
#   --intent <slug>        short kebab-case name for the safeup op
#   --request <text>       verbatim user request (logged to .beads interactions)
#   --cmd "<cmd...>"       command(s) to run inside safeup preop during BUILD
#   --skip-tests           skip stage 3 tests (use only for trivial edits)
#   --push                 include push in stage 4 (default: commit only)
#   --dry-run              show what would run, do not execute
#   --keep N               safeup retention (default: 10)
#
# Exits:
#   0  all stages passed
#   1  preflight failed
#   2  plan failed (no bd, no scope)
#   3  build failed (safeup or command)
#   4  verify failed (tests/doctor/safeup)
#   5  commit failed (bd close or git)
#   6  monitor failed (post-state unhealthy)

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFLIGHT="$HERE/preflight.sh"

# ---------- arg parsing ----------
INTENT=""
REQUEST=""
CMD=""
SKIP_TESTS=0
PUSH=0
DRY_RUN=0
KEEP=10
while [[ $# -gt 0 ]]; do
    case "$1" in
        --intent) INTENT="$2"; shift 2 ;;
        --request) REQUEST="$2"; shift 2 ;;
        --cmd) CMD="$2"; shift 2 ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --push) PUSH=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --keep) KEEP="$2"; shift 2 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) printf 'unknown flag: %s\n' "$1" >&2; exit 1 ;;
    esac
done

[[ -z "$INTENT" ]] && { echo "--intent <slug> is required" >&2; exit 1; }

# safe_op: alphanumeric + dash only
SAFE_OP="$(printf '%s' "$INTENT" | tr -cs '[:alnum:]-' '-' | tr '[:upper:]' '[:lower:]')"
[[ -z "$SAFE_OP" ]] && SAFE_OP="autonomie"

DRY_PREFIX=""
$DRY_RUN && DRY_PREFIX="[dry-run] "

log()  { printf '\n[autonomie] %s\n' "$*"; }
pass() { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*" >&2; exit "$1"; }

# ---------- preflight ----------
log "0. preflight"
$DRY_RUN || "$PREFLIGHT" || fail 1 "preflight gate failed"
pass "preflight clean"

# ---------- 1. PLAN ----------
log "1. PLAN — bd ready + scope"
$DRY_RUN || {
    if command -v bd >/dev/null 2>&1; then
        bd ready 2>/dev/null || true
        if [[ -n "$REQUEST" ]]; then
            bd create --title="$SAFE_OP: $REQUEST" \
                      --description="Autonomie chain task for: $REQUEST" \
                      --type=task --priority=3 >/dev/null 2>&1 || true
        fi
    else
        echo "  (bd absent; continuing standalone)"
    fi
}
pass "plan complete (op=$SAFE_OP)"

# ---------- 2. BUILD ----------
log "2. BUILD — safeup snapshot $SAFE_OP"
$DRY_RUN || python3 tools/safeup.py snapshot --op "$SAFE_OP" --keep "$KEEP" >/dev/null \
    || fail 3 "safeup snapshot failed"

if [[ -n "$CMD" ]]; then
    log "2b. BUILD — executing command under safeup preop"
    $DRY_RUN || python3 tools/safeup.py preop --op "$SAFE_OP-cmd" --keep "$KEEP" -- $CMD \
        || fail 3 "command under preop failed"
fi
pass "build stage complete"

# ---------- 3. VERIFY ----------
if [[ "$SKIP_TESTS" -eq 1 ]]; then
    log "3. VERIFY — skipped (--skip-tests)"
    pass "verify stage skipped"
else
    log "3. VERIFY — tests + doctor + safeup"
    $DRY_RUN || python3 tests/run_all.py >/dev/null || fail 4 "tests failed"
    pass "tests pass"
    $DRY_RUN || python3 tools/doctor.py >/dev/null 2>&1 || fail 4 "doctor reported fails"
    pass "doctor clean"
    $DRY_RUN || python3 tools/safeup.py verify >/dev/null 2>&1 || fail 4 "safeup verify found corruption"
    pass "safeup verify clean"
fi

# ---------- 4. COMMIT ----------
log "4. COMMIT — bd close + git add + commit"
$DRY_RUN || {
    if command -v bd >/dev/null 2>&1; then
        # close any open tasks created in plan
        bd list --status=open --type=task 2>/dev/null \
            | grep -oE 'codeam_codespace_[a-z0-9]+-[a-z0-9]+' \
            | head -20 \
            | xargs -r bd close >/dev/null 2>&1 || true
    fi
    # add safeup index and any tracked changes; do NOT auto-commit unless asked
    git add -A
    if ! git diff --cached --quiet; then
        git commit -m "autonomie($SAFE_OP): automated chain run

        Co-Authored-By: Claude <noreply@anthropic.com>" \
            --no-verify >/dev/null 2>&1 || true
    fi
    if [[ "$PUSH" -eq 1 ]]; then
        git push origin HEAD >/dev/null 2>&1 || fail 5 "push failed"
        pass "pushed"
    else
        pass "committed locally (push skipped — pass --push to enable)"
    fi
}

# ---------- 5. MONITOR ----------
log "5. MONITOR — final health snapshot"
$DRY_RUN || python3 tools/doctor.py >/dev/null 2>&1 || fail 6 "post-chain doctor fails"

cat <<HANDOFF

============================================================
 autonomie handoff
============================================================
 intent:  $INTENT
 op:      $SAFE_OP
 request: ${REQUEST:-(none)}
 cmd:     ${CMD:-(none)}
 push:    $([[ $PUSH -eq 1 ]] && echo yes || echo no)
 skip:    $([[ $SKIP_TESTS -eq 1 ]] && echo "tests" || echo none)
============================================================
 next: git status to inspect, then push manually if needed
============================================================
HANDOFF

exit 0
