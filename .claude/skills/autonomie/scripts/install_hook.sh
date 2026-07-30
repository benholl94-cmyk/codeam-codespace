#!/usr/bin/env bash
# install_hook.sh — permbind the autonomie skill as a SessionStart hook
# so it auto-loads on every Claude Code session for this owner_repo.
#
# Idempotent: re-running replaces the prior binding cleanly.
# Backing up settings.json before edit.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"
SETTINGS="$REPO_ROOT/.claude/settings.json"
BACKUP="$SETTINGS.bak.$(date +%Y%m%dT%H%M%SZ)"

[[ -f "$SETTINGS" ]] || { echo "no $SETTINGS — cannot bind" >&2; exit 1; }

cp "$SETTINGS" "$BACKUP"
echo "[autonomie] backed up → $BACKUP"

python3 - "$SETTINGS" <<'PYEOF'
import json, sys, pathlib
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
hooks = data.setdefault("hooks", {})
ss = hooks.setdefault("SessionStart", [])
# remove any prior autonomie binding (idempotent)
ss = [g for g in ss if "autonomie" not in json.dumps(g)]
# add the new binding that prints a one-line banner when the owner_repo matches
ss.append({
    "hooks": [
        {
            "type": "command",
            "command": (
                "REMOTE=$(git -C \"$CLAUDE_PROJECT_DIR\" remote get-url origin 2>/dev/null || echo); "
                "if echo \"$REMOTE\" | grep -q 'benholl94-cmyk/codeam-codespace'; then "
                "  echo '[autonomie] owner_repo detected — full cycle skill ready "
                "(SKILL.md under .claude/skills/autonomie/)'; fi"
            ),
            "timeout": 5,
        }
    ],
    "matcher": ""
})
hooks["SessionStart"] = ss
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print("[autonomie] SessionStart hook bound")
PYEOF

echo "[autonomie] done. Re-open the session to see the banner."
