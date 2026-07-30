# Internal Surface — Access Policy

> **This document is itself internal.** It describes paths,
> mechanisms, and recovery procedures that are not exposed in
> `public/`. Anyone reading this who is not the operator or the
> operator's authorized model is in violation of the access policy.

## What is internal

Everything under `public/` is the one-way public surface.
Everything **not** under `public/` is internal.

Concretely:

| path                                  | why internal                                            |
|---------------------------------------|---------------------------------------------------------|
| `keys_material/`                      | private signing keys (Ed25519 PEMs, mode 0600)          |
| `identity/chain.jsonl`                | pseudonym-identity chain (mode 0600)                    |
| `identity/conflicts.jsonl`            | user↔AI disagreements (mode 0600)                       |
| `identity/seed`                       | operator-chosen seed for pseudonym derivation           |
| `access/*.jsonl`                      | append-only access audit log (mode 0600)                |
| `state/` and any `<state_root>/...`   | runtime state — claims, alerts, reputation, snapshots   |
| `.beads/`                             | local-first issue tracking + persistent agent memory    |
| `tools/secure_state.py`               | paper-phrase recovery (operator-only)                   |
| `tools/safeup.py`                     | snapshot/rollback helper (operator-only)                |
| `plans/`                              | working notes, plans in progress                        |
| `INTERNAL.md`                         | this document                                           |
| `*.paper-phrase`, `*.seed`, `*.pem`   | recovery material, never to leave the operator          |

## What may access internals

The runtime enforces this through `rollout_shield/unique.py`:

```python
from rollout_shield.unique import OneWayGate, is_internal_authorized

# 1. The gate refuses to surface internal paths to anyone not
#    holding the active identity token.

gate = OneWayGate(state_root=state.root)
gate.require_authorization(
    actor=actor,
    intent="read keys_material",
)
# raises PermissionError if actor is not user+model
```

`is_internal_authorized(actor)` returns `True` only when the
actor matches one of:

* `user:<operator-handle>` — the operator themselves
* `model:<model-id>` — the operator's authorized model
* `agent:<agent-id>` — an agent whose key was minted by the operator
  and whose chain link is recorded in `identity/chain.jsonl`

Any other actor gets `False` and the gate refuses the call.
This is checked at every internal-touching code path.

## What may NOT access internals

* Anyone holding a public-only credential
* Anyone without a chain-recorded agent key
* Anyone whose chain link has been revoked
* Anyone whose actor string is `external`, `public`, `unknown`,
  or absent

## Recovery

The operator can recover from a lost seed by:

1. Reading the paper phrase (via `tools/secure_state.py --status`)
2. Re-deriving the Ed25519 seed from the phrase
3. Re-sealing it with `rollout-shield identity set-seed "<phrase>"`

This procedure is documented inline in `tools/secure_state.py`.

## Audit

Every internal access is appended to `<state_root>/access/<date>.jsonl`
with the actor, intent, and result. The audit log itself is
internal — even the existence of an access is not exposed
externally. Operators can read it via
`rollout-shield audit --json` (operator-only command).

## Why this is one-way

The public surface (`public/`) contains no relative paths that
escape it. The internal surface never symlinks, never copies
back. The runtime gate is the **only** bridge, and it requires
identity proof on every call.

There is no legal way for an external reader to obtain internal
content. If you have obtained it without authorization, please
contact the operator; the audit log will already record your
access.