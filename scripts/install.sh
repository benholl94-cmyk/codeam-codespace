#!/usr/bin/env bash
# Hard-build rollout-shield into a user-local prefix at ~/usr/.
#
# Layout produced:
#
#   ~/usr/
#   ├── bin/
#   │   ├── rollout-shield            # main CLI
#   │   └── rollout-shield-daemon     # long-lived monitor launcher
#   ├── lib/
#   │   └── python/
#   │       └── rollout_shield/       # the Python package (copy, not symlink)
#   ├── share/
#   │   └── rollout-shield/
#   │       └── interface/            # HTML/JS/CSS dashboard assets
#   ├── etc/
#   │   └── rollout-shield/
#   │       ├── config.example.json   # default config template
#   │       └── rollout-shield.service # systemd --user unit
#   └── INSTALLED_AT                  # ISO timestamp of this install
#
# Idempotent: re-running overwrites a clean copy from the repo source.
# State stays at ~/.rollout-shield/ — install never touches runtime state.
#
# Usage:
#   scripts/install.sh                # install into ~/usr/
#   scripts/install.sh --prefix /tmp/x # override prefix (test only)
#   scripts/install.sh --no-path      # skip PATH hint
#
# Exit codes:
#   0 — installed cleanly
#   1 — install failed (see stderr)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${HOME}/usr"
WRITE_PATH_HINT=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)  PREFIX="$2"; shift 2 ;;
    --no-path) WRITE_PATH_HINT=0; shift ;;
    -h|--help)
      sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '[install] %s\n' "$*" >&2; }
fail() { printf '[install] FAIL: %s\n' "$*" >&2; exit 1; }

# number of parallel cp workers — default to CPU count when available,
# capped at 8 (more rarely helps; mostly metadata-bound).
N_PARALLEL="${INSTALL_PARALLEL:-8}"
if command -v nproc >/dev/null 2>&1; then
  N_PARALLEL="$(nproc)"
fi
[[ "$N_PARALLEL" -gt 8 ]] && N_PARALLEL=8
[[ "$N_PARALLEL" -lt 1 ]] && N_PARALLEL=1

[[ -d "$REPO_ROOT/rollout_shield" ]] || fail "rollout_shield/ not found at $REPO_ROOT"
[[ -d "$REPO_ROOT/rollout_shield/interface" ]] || fail "interface assets missing"

mkdir -p "$PREFIX/bin"
mkdir -p "$PREFIX/lib/python"
mkdir -p "$PREFIX/share/rollout-shield"
mkdir -p "$PREFIX/etc/rollout-shield"

log "prefix: $PREFIX"
log "parallel workers: $N_PARALLEL"

# --- 1. Copy the Python package (real copy, not symlink) ---
# Speed: parallel cp via xargs -P, --reflink=auto for CoW support,
# find -P skips __pycache__ / .pyc / cache dirs at the source.
# Strategy: create all destination directories in one pass (sequential),
# then fan out file copies in parallel.
log "copying rollout_shield/ → $PREFIX/lib/python/rollout_shield/"
rm -rf "$PREFIX/lib/python/rollout_shield"
mkdir -p "$PREFIX/lib/python/rollout_shield"
START_NS=$(date +%s%N)
# 1a. directory tree (cheap, sequential)
( cd "$REPO_ROOT" && \
  find rollout_shield \
    -type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache -o -name .ruff_cache \) -prune -o \
    -type d -print ) \
  | while read -r d; do
      mkdir -p "$PREFIX/lib/python/$d"
    done
# 1b. files only, fanned out across N_PARALLEL workers
( cd "$REPO_ROOT" && \
  find rollout_shield \
    -type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache -o -name .ruff_cache \) -prune -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -prune -o \
    -type f -print ) \
  | xargs -P "$N_PARALLEL" -I {} \
    cp -a --reflink=auto "$REPO_ROOT/{}" "$PREFIX/lib/python/{}"
END_NS=$(date +%s%N)
log "copy done in $(( (END_NS - START_NS) / 1000000 )) ms"

# --- 2. Copy the interface assets (parallel, same exclusions) ---
# Strip the leading "rollout_shield/" prefix so the destination layout
# is $PREFIX/share/rollout-shield/interface/ — not .../rollout_shield/interface/.
log "copying interface/ → $PREFIX/share/rollout-shield/interface/"
rm -rf "$PREFIX/share/rollout-shield/interface"
START_NS=$(date +%s%N)
( cd "$REPO_ROOT" && \
  find rollout_shield/interface \
    -type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache \) -prune -o \
    -type d -print ) \
  | while read -r d; do
      # strip "rollout_shield/" prefix
      rel="${d#rollout_shield/}"
      mkdir -p "$PREFIX/share/rollout-shield/$rel"
    done
( cd "$REPO_ROOT" && \
  find rollout_shield/interface \
    -type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache \) -prune -o \
    -type f -print ) \
  | xargs -P "$N_PARALLEL" -I {} \
    sh -c 'rel="${1#rollout_shield/}"; cp -a --reflink=auto "$1" "$2/$rel"' _ {} "$PREFIX/share/rollout-shield"
END_NS=$(date +%s%N)
log "copy done in $(( (END_NS - START_NS) / 1000000 )) ms"

# --- 2b. Pre-compile .pyc files (warm cache for the daemon + dashboard) ---
# Stdlib only — uses compileall. Idempotent: re-running overwrites.
PYTHON_BIN="${PYTHON_BIN:-python3}"
if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  log "pre-compiling .pyc → $PREFIX/lib/python/rollout_shield/"
  "$PYTHON_BIN" -m compileall -q -j "$N_PARALLEL" \
    "$PREFIX/lib/python/rollout_shield" 2>/dev/null || \
    log "pre-compile skipped (compileall failed)"
fi

# --- 3. Copy the CLI entry scripts (rewritten shims) ---
log "writing CLI shims → $PREFIX/bin/"

write_cli_shim() {
  local name="$1" target_module="$2"
  cat > "$PREFIX/bin/$name" <<EOF
#!/usr/bin/env python3
# Auto-generated by scripts/install.sh — hard build shim.
# Targets the rollout_shield package installed at:
#   $PREFIX/lib/python/rollout_shield/
# Do not edit by hand — re-run scripts/install.sh to regenerate.

import os
import sys
from pathlib import Path

_PREFIX = Path("$PREFIX").expanduser()
_PKG_PARENT = _PREFIX / "lib" / "python"
_REPO_PKG_PARENT = Path("$REPO_ROOT").expanduser()

# Search order:
#   1. ~/usr/lib/python  (this install — wins)
#   2. <repo>/rollout_shield  (dev fallback, appended AFTER the install)
# Insert the install at index 0 FIRST so it shadows the repo source.
if (_PKG_PARENT / "rollout_shield").is_dir():
    sys.path.insert(0, str(_PKG_PARENT))
# The repo fallback is appended (NOT inserted at 0) so the install
# always wins for duplicate module names.
if (_REPO_PKG_PARENT / "rollout_shield").is_dir():
    repo_str = str(_REPO_PKG_PARENT)
    if repo_str not in sys.path:
        sys.path.append(repo_str)

try:
    from rollout_shield.$target_module import main
except ImportError as exc:
    sys.stderr.write(
        f"$name: failed to import rollout_shield.$target_module ({exc}).\\n"
        f"  searched: {_PKG_PARENT}, {_REPO_PKG_PARENT}\\n"
        f"  reinstall with: scripts/install.sh\\n"
    )
    raise

if __name__ == "__main__":
    sys.exit(main())
EOF
  chmod 0755 "$PREFIX/bin/$name"
}

write_cli_shim rollout-shield cli
write_cli_shim rollout-shield-monitor monitor_daemon

# backward-compatible alias the existing systemd / launchd units reference
ln -sf rollout-shield "$PREFIX/bin/rollout-shield-daemon" 2>/dev/null || true

# --- 4. Install default config + systemd --user unit ---
log "writing default config + systemd unit → $PREFIX/etc/rollout-shield/"

cat > "$PREFIX/etc/rollout-shield/config.example.json" <<'EOF'
{
  "schema_version": 1,
  "monitor_interval_seconds": 60,
  "alert_webhook_url": "",
  "claim_retention_days": 2555,
  "health_window_seconds": 300,
  "reputation_decay_days": 30,
  "self_heal_enabled": true,
  "self_heal_interval_cycles": 5
}
EOF

cat > "$PREFIX/etc/rollout-shield/rollout-shield.service" <<'EOF'
[Unit]
Description=rollout-shield persistent monitor
After=network-online.target

[Service]
Type=simple
ExecStart=%h/usr/bin/rollout-shield-monitor --daemon --foreground --interval 60
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# --- 5. Stamp the install ---
date -u +%Y-%m-%dT%H:%M:%SZ > "$PREFIX/INSTALLED_AT"
echo "$REPO_ROOT" > "$PREFIX/REPO_SOURCE"

# --- 5b. Smart-routing manifest (government-version binding) ---
# Introspect the AI layer at install time and stamp a manifest that
# binds the official build to a known set of models + strategies.
# Operators can inspect the binding with `rollout-shield routing`.
log "writing smart-routing manifest → $PREFIX/etc/rollout-shield/smart-routing.json"
"$PYTHON_BIN" - "$REPO_ROOT" "$PREFIX" <<'PY'
import json, sys, hashlib, datetime, importlib
repo_root, prefix = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo_root)
try:
    import rollout_shield.ai.models as m
    import rollout_shield.ai.own_models as own
    bound = m.list_models()
    own_ids = [info.id for info in bound if info.family == "own"]
    mock_ids = [info.id for info in bound if info.family == "mock"]
except Exception as exc:
    # fallback when the AI layer is partially broken — manifest still
    # stamps a valid envelope so the runtime can fall back gracefully.
    bound, own_ids, mock_ids = [], [], []
    print(f"[install] WARNING: could not introspect AI layer ({exc})", file=sys.stderr)

manifest = {
    "schema_version": 1,
    "build_tier": "government",
    "controller_policy": "shared",
    "default_strategy": "best",
    "bound_families": ["own", "mock"],
    "bound_models": sorted(own_ids + mock_ids),
    "priority_order": ["own", "mock"],
    "routing_profiles": {
        "shared":      {"strategy": "best",      "families": ["own", "mock"]},
        "device-only": {"strategy": "consensus", "families": ["own"]},
        "human-only":  {"strategy": "first",     "families": ["mock", "own"]},
    },
    "installed_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "repo_source":  repo_root,
}
# stamp signature: sha256 of canonical manifest (minus the signature field)
canon = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
manifest["manifest_signature"] = "sha256:" + hashlib.sha256(canon).hexdigest()
with open(f"{prefix}/etc/rollout-shield/smart-routing.json", "w") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
print(f"[install] manifest_signature={manifest['manifest_signature']}")
print(f"[install] bound_models={len(manifest['bound_models'])}")
PY

# --- 6. Verify the install is callable ---
log "verifying install…"
if ! "$PREFIX/bin/rollout-shield" --version >/dev/null 2>&1; then
  fail "post-install verification failed — $PREFIX/bin/rollout-shield --version did not exit 0"
fi

log "installed: $("$PREFIX/bin/rollout-shield" --version)"

# --- 6b. Verify the smart-routing binding is callable ---
log "verifying smart-routing binding…"
if ! "$PREFIX/bin/rollout-shield" routing >/dev/null 2>&1; then
  log "WARNING: rollout-shield routing did not exit 0 (manifest may be missing)"
fi

# --- 7. PATH hint (opt-out via --no-path) ---
if [[ "$WRITE_PATH_HINT" -eq 1 ]]; then
  if [[ ":$PATH:" != *":$PREFIX/bin:"* ]]; then
    log "PATH: $PREFIX/bin is not on PATH"
    log "      add with:  echo 'export PATH=\"\$HOME/usr/bin:\$PATH\"' >> ~/.bashrc"
    log "      or one-shot (current shell): export PATH=\"$PREFIX/bin:\$PATH\""
  else
    log "PATH: $PREFIX/bin already on PATH"
  fi
fi

cat <<EOF

[install] OK — rollout-shield hard-built into $PREFIX
[install] smart-routing bound: $PREFIX/etc/rollout-shield/smart-routing.json
[install] verify with: scripts/verify-install.sh
[install] inspect binding:  $PREFIX/bin/rollout-shield routing
[install] uninstall:  scripts/uninstall.sh
EOF
