# Claims Format Reference

> **Companion to**: `README.md` (Claims Protocol overview)
> **Status**: Normative reference

This document specifies the canonical JSON format for a claim produced
under the Claims Protocol. It is the source of truth for the wire format;
the JSON Schemas in `schemas/` are derived from this document.

## Top-level structure

Every claim is a JSON object with these top-level fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `protocol_version` | string | yes | Semantic version of the protocol spec this claim conforms to. Current: `"0.1.0"`. |
| `claim_type` | string | yes | One of the registered claim types (see `README.md` §"Claim types"). |
| `claim_id` | string | yes | ULID-prefixed identifier, unique within the agent's namespace. |
| `agent_id` | string | yes | Reference to an agent identity declared in `agent/identity.json`. |
| `issued_at` | string | yes | ISO 8601 UTC timestamp of claim creation. |
| `parent_hashes` | array<string> | yes | Content hashes (SHA-256, hex) of parent claims this one builds on. Empty array for genesis claims. |
| `beads_issue_id` | string | optional | Reference to a Beads issue this claim pertains to, if any. |
| `body` | object | yes | Type-specific payload. Schema varies by `claim_type`. |
| `signature` | object | yes | Ed25519 signature over the canonical JSON (RFC 8785) of all fields except `signature` itself. |

## Signature structure

```json
{
  "key_id": "agent-key-1",
  "algorithm": "ed25519",
  "value": "<base64-encoded 64-byte signature>",
  "signed_at": "2026-07-29T01:23:45Z"
}
```

- `key_id` references one of the public keys declared in
  `agent/identity.json` under `keys[].id`.
- `value` is base64-encoded; decoding yields exactly 64 bytes.
- The signature is computed over the JCS (RFC 8785) canonical form of
  the claim object with the `signature` field removed.

## Body schemas (core types)

### intent

Issued by humans (or upstream systems) to declare a task to be done.

```json
{
  "summary": "Wire backend pairing in on-create.sh",
  "rationale": "TODO marker exists; needed for codespace bootstrap.",
  "acceptance": [
    "codeam pair-auto is invoked",
    "token sourced from env var, not argv",
    "verified end-to-end in a fresh codespace"
  ]
}
```

### change

Issued by agents to declare a diff applied to the repo.

```json
{
  "diff_hash": "sha256:<hex of unified diff>",
  "files_touched": [".devcontainer/on-create.sh"],
  "lines_added": 12,
  "lines_removed": 3,
  "summary": "Adds codeam pair-auto invocation with safe token handling"
}
```

### test

Issued by agents to declare the result of running a test suite.

```json
{
  "test_suite": "scripts/verify-claim.sh",
  "exit_code": 0,
  "output_hash": "sha256:<hex of stdout+stderr>",
  "duration_seconds": 1.42,
  "summary": "All claims in .beads/claims/ verify successfully"
}
```

### verify

Issued by humans or other agents to confirm a prior `change` claim.

```json
{
  "verified_claim_hash": "sha256:<hex>",
  "method": "manual-review",
  "reviewer_notes": "Diff is minimal; pairs with the existing TODO marker."
}
```

### contradict

Issued by humans or other agents to reject a prior `change` claim.

```json
{
  "contradicted_claim_hash": "sha256:<hex>",
  "reason_code": "incorrect-output",
  "explanation": "Token is sourced from argv, violating acceptance criterion 2."
}
```

### delegate

Issued by an agent to hand off responsibility for a Beads issue.

```json
{
  "delegate_to": "agent-id-of-successor",
  "beads_issue_id": "codeam_codespace_3021190f-abc",
  "context_summary": "Pairing wiring is 80% done; remaining is verification step."
}
```

## Hashing rules

All hashes in the protocol use SHA-256 over the canonical UTF-8 byte
sequence of the referenced content, encoded as lowercase hex with a
`sha256:` prefix.

For claims themselves, the "content" is the JCS canonical form of the
claim object (excluding the `signature` field). This is what gets
hashed and referenced by `parent_hashes`.

## Canonicalization (RFC 8785)

The JCS canonical form of a JSON object is determined by:

1. Object keys are sorted lexicographically (byte-wise UTF-8).
2. Arrays preserve order.
3. Numbers are encoded per RFC 8785 (no leading zeros, etc.).
4. Strings are passed through unchanged (no whitespace normalization).
5. No insignificant whitespace is included.
6. UTF-8 encoding throughout.

Reference implementation: any RFC 8785-compliant library.

## Field validation rules

- `claim_id` MUST be a valid ULID (26-character Crockford base32).
- `issued_at` MUST be ISO 8601 UTC with `Z` suffix.
- `parent_hashes` MAY be empty only for `intent` claims (the root of
  a claim graph).
- `beads_issue_id` MUST match the regex
  `^[a-z0-9_-]+-[a-z0-9]+$` (project prefix + dash + base36 counter).
- `body` MUST validate against the corresponding type schema in
  `protocol/schemas/`.

## Extension mechanism

Implementations MAY add additional fields to `body` for new sub-types,
provided:

- The new field has a `x_` prefix (e.g., `x_custom_metric`).
- The schema in `protocol/schemas/` is updated to include the field.
- The protocol version is bumped (minor version for additive changes).
