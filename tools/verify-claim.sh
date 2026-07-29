#!/usr/bin/env bash
# verify-claim.sh — verify a claim file against the Claims Protocol v0.1.
#
# Usage:
#   ./tools/verify-claim.sh path/to/claim.json
#   ./tools/verify-claim.sh --all            # verify every claim under .beads/claims/
#   ./tools/verify-claim.sh --strict ...     # fail on warnings (not just hard errors)
#
# Checks performed (in order):
#   1. JSON parse
#   2. Schema validation (against protocol/schemas/claim.schema.json)
#   3. Field-level checks (ULID, ISO-8601 timestamps, hash formats)
#   4. Signature verification (Ed25519 against agent/identity.json public key)
#   5. Parent-hash existence (every referenced parent must exist in the graph)
#   6. Beads-issue existence (every referenced beads_issue_id must exist)
#
# Exit codes:
#   0  = claim is valid
#   1  = claim is invalid (hard error)
#   2  = claim is valid but with warnings (only with --strict)
#
# Dependencies (install on first run):
#   pip install jsonschema pynacl rfc8785

set -euo pipefail

log() { echo "[verify-claim] $*" >&2; }
err() { echo "[verify-claim] ERROR: $*" >&2; exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SCHEMA="${REPO_ROOT}/protocol/schemas/claim.schema.json"
IDENTITY="${REPO_ROOT}/agent/identity.json"

[ -f "${SCHEMA}" ] || err "schema not found: ${SCHEMA}"
[ -f "${IDENTITY}" ] || err "identity not found: ${IDENTITY}"

# Ensure jsonschema is available; install on demand if pip is available.
python3 -c "import jsonschema" 2>/dev/null || {
  log "jsonschema not installed; attempting 'pip install jsonschema'"
  pip install --quiet jsonschema 2>&1 | tail -3 >&2 || true
}

verify_one() {
  local CLAIM_FILE="$1"
  local STRICT="${STRICT:-false}"

  log "verifying ${CLAIM_FILE}"
  python3 - "$CLAIM_FILE" "$SCHEMA" "$IDENTITY" "${REPO_ROOT}" "${STRICT}" <<'PYTHON_SCRIPT'
import sys, json, hashlib, re, os, base64
try:
    import jsonschema
except ImportError:
    print("ERROR: jsonschema not installed; run: pip install jsonschema", file=sys.stderr)
    sys.exit(1)

claim_path, schema_path, identity_path, repo_root, strict = sys.argv[1:6]
strict = strict.lower() == "true"

with open(claim_path) as f:
    claim = json.load(f)
with open(schema_path) as f:
    schema = json.load(f)
with open(identity_path) as f:
    identity = json.load(f)

errors = []
warnings = []

# 2. Schema validation
try:
    jsonschema.validate(instance=claim, schema=schema)
except jsonschema.ValidationError as e:
    errors.append("schema validation failed: " + e.message)

# 3. Field-level checks
def check(pattern, value, label):
    if not re.fullmatch(pattern, value):
        errors.append(label + "=" + repr(value) + " does not match " + pattern)

if "claim_id" in claim:
    check(r"^[0-9A-HJKMNP-TV-Z]{26}$", claim["claim_id"], "claim_id")
if "issued_at" in claim:
    check(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$",
          claim["issued_at"], "issued_at")
for h in claim.get("parent_hashes", []):
    check(r"^sha256:[a-f0-9]{64}$", h, "parent_hash")

# 4. Signature verification (Ed25519)
sig = claim.get("signature", {})
key_id = sig.get("key_id")
algo = sig.get("algorithm")
sig_value = sig.get("value")
if algo and algo != "ed25519":
    errors.append("unsupported signature algorithm: " + repr(algo))
elif sig_value and key_id:
    pub_keys = {pk["id"]: pk for pk in identity.get("public_keys", [])}
    if key_id not in pub_keys:
        errors.append("signature key_id " + repr(key_id) + " not declared in identity.json")
    else:
        pub_b64 = pub_keys[key_id]["value"]
        body = {k: v for k, v in claim.items() if k != "signature"}
        try:
            from rfc8785 import canonicalize
            canonical = canonicalize(body)
        except ImportError:
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            warnings.append("rfc8785 not installed; using minimal canonical form (signature verification may be approximate)")

        try:
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError
            vk = VerifyKey(base64.b64decode(pub_b64))
            try:
                vk.verify(canonical, base64.b64decode(sig_value))
            except BadSignatureError:
                errors.append("signature does not match body (Ed25519 verify failed)")
        except ImportError:
            warnings.append("PyNaCl not installed; cannot verify signature (install with: pip install pynacl)")

# 5. Parent-hash existence
claims_root = os.path.join(repo_root, ".beads", "claims")
for parent_hash in claim.get("parent_hashes", []):
    found = False
    if os.path.isdir(claims_root):
        for root, _, files in os.walk(claims_root):
            for fn in files:
                if not fn.endswith(".jsonl"):
                    continue
                fp = os.path.join(root, fn)
                with open(fp) as f:
                    for line in f:
                        try:
                            c = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        body = {k: v for k, v in c.items() if k != "signature"}
                        try:
                            from rfc8785 import canonicalize
                            canonical = canonicalize(body)
                        except ImportError:
                            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        if hashlib.sha256(canonical).hexdigest() == parent_hash.split(":", 1)[1]:
                            found = True
                            break
                if found:
                    break
            if found:
                break
    if not found:
        warnings.append("parent_hash " + parent_hash + " not found in claim graph (graph may be incomplete)")

# 6. Beads issue existence (if bd available)
beads_id = claim.get("beads_issue_id")
if beads_id:
    try:
        import subprocess
        r = subprocess.run(["bd", "show", beads_id], capture_output=True, text=True, cwd=repo_root)
        if r.returncode != 0:
            warnings.append("beads_issue_id " + beads_id + " not found via 'bd show'")
    except FileNotFoundError:
        warnings.append("bd not on PATH; skipping beads_issue_id existence check")

# Report
print("claim:        " + claim_path)
print("claim_type:   " + str(claim.get("claim_type", "?")))
print("claim_id:     " + str(claim.get("claim_id", "?")))
print("agent_id:     " + str(claim.get("agent_id", "?")))
print("errors:       " + str(len(errors)))
for e in errors:
    print("  - " + e)
print("warnings:     " + str(len(warnings)))
for w in warnings:
    print("  - " + w)

if errors:
    sys.exit(1)
if warnings and strict:
    sys.exit(2)
sys.exit(0)
PYTHON_SCRIPT
}

STRICT="false"

case "${1:-}" in
  --all)
    if [ ! -d "${REPO_ROOT}/.beads/claims" ]; then
      log "no claims directory; nothing to verify"
      exit 0
    fi
    TOTAL=0; FAILED=0
    while IFS= read -r -d '' CLAIM; do
      TOTAL=$((TOTAL + 1))
      if ! verify_one "${CLAIM}"; then
        FAILED=$((FAILED + 1))
      fi
    done < <(find "${REPO_ROOT}/.beads/claims" -name '*.jsonl' -print0 2>/dev/null || true)
    log "verified ${TOTAL} files, ${FAILED} failed"
    [ "${FAILED}" -eq 0 ]
    ;;
  --strict)
    shift
    STRICT="true"
    verify_one "$1"
    ;;
  -h|--help|"")
    cat <<EOF
Usage:
  $0 path/to/claim.json
  $0 --all
  $0 --strict path/to/claim.json
EOF
    exit 1
    ;;
  *)
    verify_one "$1"
    ;;
esac
