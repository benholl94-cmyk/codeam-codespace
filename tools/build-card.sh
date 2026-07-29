#!/usr/bin/env bash
# build-card.sh — generate an Ed25519 keypair and (optionally) update identity.json.
#
# Usage:
#   ./tools/build-card.sh                  # generate, print keys, do NOT modify identity.json
#   ./tools/build-card.sh --apply          # generate, update identity.json in place
#   ./tools/build-card.sh --print-public   # only print the public key (uses existing key or generates one)
#
# Output (always):
#   - Private key (32 bytes, base64) — printed ONCE; user must store it
#   - Public key  (32 bytes, base64) — safe to publish
#
# Security note: the private key is printed to stdout. Do not redirect to a
# file that gets committed; store it in a local secrets manager or the
# workspace's gitignored secrets directory.

set -euo pipefail

log() { echo "[build-card] $*"; }
err() { echo "[build-card] ERROR: $*" >&2; exit 1; }

# Try multiple Ed25519 generators in order of preference.
generate_keypair() {
  if command -v openssl >/dev/null 2>&1; then
    # openssl genpkey supports Ed25519 from 1.1.1 onward.
    TMPDIR_KEYGEN="$(mktemp -d)"
    trap 'rm -rf "${TMPDIR_KEYGEN}"' EXIT
    openssl genpkey -algorithm Ed25519 -out "${TMPDIR_KEYGEN}/priv.pem" 2>/dev/null || return 1
    openssl pkey -in "${TMPDIR_KEYGEN}/priv.pem" -outform DER 2>/dev/null | tail -c 32 | base64 -w 0 > "${TMPDIR_KEYGEN}/priv.b64" || return 1
    openssl pkey -in "${TMPDIR_KEYGEN}/priv.pem" -pubout -outform DER 2>/dev/null | tail -c 32 | base64 -w 0 > "${TMPDIR_KEYGEN}/pub.b64" || return 1
    PRIV_B64="$(cat "${TMPDIR_KEYGEN}/priv.b64")"
    PUB_B64="$(cat "${TMPDIR_KEYGEN}/pub.b64")"
    return 0
  fi
  if python3 -c "from nacl.signing import SigningKey" >/dev/null 2>&1; then
    OUT="$(python3 - <<'PY'
import base64
from nacl.signing import SigningKey
sk = SigningKey.generate()
pk = sk.verify_key
print(base64.b64encode(bytes(sk)).decode())
print(base64.b64encode(bytes(pk)).decode())
PY
)"
    PRIV_B64="$(echo "${OUT}" | sed -n '1p')"
    PUB_B64="$(echo "${OUT}" | sed -n '2p')"
    return 0
  fi
  return 1
}

# Run generator.
if ! generate_keypair; then
  err "No Ed25519 generator available. Install openssl >= 1.1.1 or 'pip install pynacl'."
fi

MODE="${1:-}"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
IDENTITY_FILE="${REPO_ROOT}/agent/identity.json"

log "generated Ed25519 keypair"
log "  public  key (32 B, base64): ${PUB_B64}"
log "  private key (32 B, base64): ${PRIV_B64}  <-- store securely, do not commit"

case "${MODE}" in
  --print-public)
    log "(mode: --print-public, no file changes)"
    exit 0
    ;;
  --apply)
    log "(mode: --apply, updating ${IDENTITY_FILE})"
    if [ ! -f "${IDENTITY_FILE}" ]; then
      err "identity file not found: ${IDENTITY_FILE}"
    fi
    # Use Python for safe JSON edit (avoids sed regex escaping issues).
    python3 - <<PY
import json, sys
path = "${IDENTITY_FILE}"
with open(path) as f:
    doc = json.load(f)
if "public_keys" not in doc or not doc["public_keys"]:
    print("[build-card] ERROR: identity.json has no public_keys array", file=sys.stderr)
    sys.exit(1)
doc["public_keys"][0]["value"] = "${PUB_B64}"
with open(path, "w") as f:
    json.dump(doc, f, indent=2, sort_keys=False)
    f.write("\n")
print("[build-card] updated identity.json public_keys[0].value")
PY
    log "next steps:"
    log "  1. verify: jq '.public_keys[0].value' ${IDENTITY_FILE}"
    log "  2. commit: git add agent/identity.json && git commit -m 'agent: rotate signing key'"
    log "  3. (optional) store the private key in your secrets manager"
    log "  4. NEVER commit the private key — it is printed above ONCE"
    exit 0
    ;;
  "")
    log "(default mode: print only, no file changes)"
    log "to apply this keypair to identity.json, re-run with --apply"
    exit 0
    ;;
  *)
    err "unknown mode: ${MODE}. Use --apply or --print-public or no flag."
    ;;
esac
