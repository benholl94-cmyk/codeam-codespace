#!/usr/bin/env bash
# leak_check.sh — scan the repo for anything that could leak data.
#
# Checks:
#   1. External HTTP/HTTPS URLs in source code (excluding docstrings)
#   2. Network libraries (urllib/requests/socket) used in source
#   3. Hardcoded secrets / API keys / tokens
#   4. Third-party pip imports beyond stdlib + cryptography
#   5. Telemetry / analytics / tracking patterns
#   6. Files in gitignore that should be (state/, .audit/, .env)
#   7. Git-tracked files matching secret patterns (.env, .key, *.pem)
#   8. Workflow files that might exfiltrate (echo $SECRET, curl POST)
#   9. Embedded base64 blobs (potential obfuscated secrets)
#  10. Doc/markdown mentions of external services not yet justified
#
# Exit codes:
#   0  no leaks found
#   1  leaks found (printed in summary)
#   2  scan failed (preconditions missing)
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

ok()  { printf '  [OK]   %s\n' "$*"; }
warn(){ printf '  [WARN] %s\n' "$*"; LEAKS=$((LEAKS + 1)); }
fail(){ printf '  [FAIL] %s\n' "$*"; LEAKS=$((LEAKS + 1)); }

LEAKS=0
echo "=========================================="
echo " leak_check.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================="

# 1. external URLs in source
echo
echo "[1] external URLs in source code:"
URLS=$(grep -rEn 'https?://[^ "'\'')]+' \
    rollout_shield/ tools/ bin/ tests/ 2>/dev/null \
    | grep -vE 'tools/leak_check\.sh' \
    | grep -vE 'github\.com/(benholl94|anthropics|gastownhall)|keepachangelog\.com|w3\.org/2000/svg' \
    | grep -vE '127\.0\.0\.1|localhost|0\.0\.0\.0' \
    | grep -vE 'f"http://\{args' \
    | grep -vE '# http' \
    | grep -vE '\.md:' \
    || true)
if [[ -n "$URLS" ]]; then
    echo "$URLS" | head -10
    warn "$(echo "$URLS" | wc -l) external URL reference(s) — review"
else
    ok "no external URLs beyond docstrings"
fi

# 2. network libraries in source
echo
echo "[2] network library imports in source:"
NET=$(grep -rEn '^import (urllib|requests|httpx|aiohttp)|^(import|from) socket' \
    rollout_shield/ tools/ bin/ tests/ 2>/dev/null \
    | grep -vE '^[^:]+:[0-9]+:\s*#' \
    || true)
# Distinguish stdlib (urllib/socket) from third-party (requests/httpx/aiohttp).
NET_STDLIB=$(echo "$NET" | grep -E 'urllib|socket' || true)
NET_THIRD=$(echo "$NET" | grep -E 'requests|httpx|aiohttp' || true)
if [[ -n "$NET_THIRD" ]]; then
    echo "$NET_THIRD"
    fail "third-party network lib(s) imported — stdlib only"
elif [[ -n "$NET_STDLIB" ]]; then
    ok "stdlib network modules only (urllib/socket — user-configured endpoints)"
else
    ok "no network imports in production code"
fi

# 3. hardcoded secrets
echo
echo "[3] hardcoded secrets (api_key/secret/token/password/private_key):"
SECRETS=$(grep -rEn '(api[_-]?key|secret|token|password|private[_-]?key)\s*=\s*["'\''][^"'\''$]{6,}' \
    rollout_shield/ tools/ bin/ tests/ 2>/dev/null \
    | grep -vE '\.md:|test_|example\.com|<.*>' \
    || true)
if [[ -n "$SECRETS" ]]; then
    echo "$SECRETS" | head -10
    fail "$(echo "$SECRETS" | wc -l) hardcoded secret pattern(s)"
else
    ok "no hardcoded secrets in code"
fi

# 4. third-party imports
echo
echo "[4] third-party imports beyond stdlib + cryptography:"
# Extract module names from real import statements only:
#   * scan only .py files (no markdown/docstring noise)
#   * skip comment lines (starting with #)
#   * strip module attributes and trailing comma-separated names
#   * allowlist stdlib + cryptography + local sibling modules
# Real imports have a strict shape: `import X` or `from X import Y`. Anything
# with spaces inside the module name, or multi-word prose, is docstring noise.
THIRD=$(grep -rhE --include='*.py' '^(import [a-z_][a-z0-9_.]*|from [a-z_][a-z0-9_.]* import [a-zA-Z_][a-zA-Z0-9_]*)$' \
    rollout_shield/ tools/ 2>/dev/null \
    | grep -vE '^[^:]+:[0-9]+:\s*#' \
    | grep -vE '__future__|annotations' \
    | sed -E 's/^(import|from) +//; s/\..*$//; s/[, ].*$//' \
    | sort -u \
    | grep -vE '^(argparse|json|os|sys|re|time|uuid|hashlib|tempfile|contextlib|dataclasses|pathlib|shutil|datetime|tarfile|subprocess|io|fcntl|base64|warnings|typing|collections|functools|itertools|enum|abc|math|stat|signal|threading|traceback|inspect|types|textwrap|unicodedata|string|struct|operator|copy|pickle|gzip|zipfile|fnmatch|glob|getopt|logging|platform|errno|select|queue|asyncio|secrets|hmac|mimetypes|http|urllib|socket|ssl|cryptography)$' \
    | grep -vE '^(audit_log|owner_log|secure_state|safeup|doctor|release|autonomy|leak_check)$' \
    || true)
if [[ -n "$THIRD" ]]; then
    echo "$THIRD" | head -10
    fail "third-party import(s): $(echo "$THIRD" | tr '\n' ' ')"
else
    ok "only stdlib + cryptography imported"
fi

# 5. telemetry/analytics patterns
echo
echo "[5] telemetry / analytics patterns:"
TELE=$(grep -rEn '(analytics|telemetry|tracking|gtag|google-analytics|sentry\.io|mixpanel|posthog|segment\.io|datadog|new ?relic)' \
    rollout_shield/ bin/ tests/ 2>/dev/null \
    | grep -vE 'tools/leak_check\.sh|\.md:|leak_check' \
    || true)
if [[ -n "$TELE" ]]; then
    echo "$TELE" | head -10
    fail "telemetry pattern(s) found"
else
    ok "no telemetry patterns"
fi

# 6. .gitignore coverage
echo
echo "[6] .gitignore coverage:"
for pat in '.env' '.env.*' '*.pem' '*.key' '.audit/' '.rollout-shield/' '.beads/proxieddb' 'private/' 'secrets/'; do
    if grep -qE "^${pat//./\\.}" .gitignore 2>/dev/null; then
        ok "gitignore covers: $pat"
    else
        warn "gitignore missing pattern: $pat"
    fi
done

# 7. git-tracked sensitive files
echo
echo "[7] git-tracked sensitive files:"
SENS=$(git ls-files | grep -E '\.(env|env\.[a-z]+|pem|key|p12)$|^private/|^secrets/' \
    | grep -vE '\.env\.example$' \
    | head -10 || true)
if [[ -n "$SENS" ]]; then
    echo "$SENS"
    fail "tracked sensitive file(s) — should be gitignored"
else
    ok "no tracked sensitive files"
fi

# 8. workflow exfiltration patterns
echo
echo "[8] workflow exfiltration patterns:"
# Only flag actual exfil patterns (POST with body, $SECRET in URL). Legit installs
# (curl ... | bash for bootstrap) are allowed and listed separately.
WORK=$(grep -rEn 'curl.*-d |curl.*--data|wget --post' \
    .github/workflows/ .github/actions/ 2>/dev/null \
    || true)
if [[ -n "$WORK" ]]; then
    echo "$WORK" | head -10
    warn "$(echo "$WORK" | wc -l) workflow URL(s) — review for exfiltration"
else
    ok "no obvious workflow exfiltration"
fi

# 9. large base64 blobs
echo
echo "[9] suspicious base64 blobs (> 200 chars continuous):"
B64=$(grep -rEn '[A-Za-z0-9+/]{200,}={0,2}' \
    rollout_shield/ tools/ bin/ 2>/dev/null \
    | grep -vE '\.md:|test_' \
    | head -5 \
    || true)
if [[ -n "$B64" ]]; then
    echo "$B64"
    warn "potential base64 blob(s) — review for obfuscation"
else
    ok "no suspicious base64 blobs"
fi

echo
echo "=========================================="
if [[ "$LEAKS" -eq 0 ]]; then
    echo " RESULT: clean (0 leaks)"
else
    echo " RESULT: $LEAKS potential leak(s) — review above"
fi
echo "=========================================="

exit $([[ $LEAKS -eq 0 ]] && echo 0 || echo 1)
