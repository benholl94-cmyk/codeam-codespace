# Agent Layer

This directory holds the cryptographic identity and capability declarations
for AI coding agents that work in this workspace.

## Identity model

An agent identity is the **long-lived cryptographic anchor** that ties all
output from a single conceptual agent together, across sessions, machines,
and repositories.

The anchor is an **Ed25519 keypair** generated locally; the private key
never leaves the workspace. The public key is published in `identity.json`
and forms the basis for signature verification of every claim the agent
produces.

## Binding to git committer identity

The agent's identity is **bound** to the git committer identity declared
in this repository's local config (see `bound_committer` in
`identity.json`). This binding means:

- When the agent signs a claim, the signature can be cross-referenced
  to the commit chain that contains the agent's commits.
- If the committer identity changes (e.g., the workspace is reopened as
  a different user), the binding becomes invalid and a new agent
  identity must be provisioned.
- The binding is **advisory**, not enforced cryptographically — a
  malicious agent could rewrite `identity.json`. The protocol's
  security model assumes that the workspace itself is trusted; the
  cryptography defends against accidental tampering, not malicious
  insiders.

## Key rotation

Keys may be rotated by appending a new entry to `public_keys[]` with
a fresh `valid_from` timestamp and a new `id`. Old keys are retained
in the array with their `valid_until` set; they continue to verify
signatures made during their validity period but cannot sign new
claims. This is similar to DKIM key rotation in email systems.

Reputation **transfers** across key rotations if the rotation is
declared in `key_history[]` (see `identity.json._meta.note` for the
status of this field; it is currently a placeholder).

## File layout

```
agent/
├── README.md          # this file
└── identity.json      # the agent's published capability card
```

The claims themselves are stored separately, under
`.beads/claims/<agent_id>/<year>-<month>.jsonl`. See `protocol/README.md`
§"Storage".

## Provisioning workflow

1. Run `tools/build-card.sh` to generate a fresh Ed25519 keypair.
2. The tool prints the public key (base64-encoded, 32 bytes).
3. Update `identity.json` `public_keys[0].value` with the printed key.
4. (Optional) Update `display_name` and `description` to match the
   agent's actual purpose.
5. Commit `identity.json` to the repo so the public key is preserved.

## What this is NOT

- This is NOT a substitute for proper secrets management. The private
  key should be generated locally and never committed to git.
- This is NOT a way to attribute output to a human author. The agent
  identity is distinct from the human committer identity.
- This is NOT a replacement for code review. Cryptographic signatures
  prove authorship, not correctness.
