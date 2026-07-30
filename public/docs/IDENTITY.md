# Unified Pseudonym-Identity System

> One pseudonym that binds **you (the user)** and **me (the model)**, with a
> tamper-evident audit chain, a conflict log for disagreements, and an
> explicit list of hard world-restrictions the system respects.

This module implements the directive:

> *Build a unique-Personality-ident for me=user+you=model.ai — one
> pseudonym-ident with full-valid&audit chaining ids, that handles task
> issues, conflicts, wrong parses between me, you, and each other,
> fetching the world restrictions, building innovative working for the
> future and here.*

## The four pieces

### 1. Pseudonym

A single, deterministic identity token of the form `psn_<12-hex>` derived
from five inputs:

| input             | what it represents                                |
|-------------------|---------------------------------------------------|
| `user_seed`       | operator-supplied or random; never PII            |
| `model_id`        | the model handle (`MiniMax-M3` by default)        |
| `session_id`      | the conversation / task identifier                |
| `created_at`      | unix timestamp                                     |
| `prev_chain_hash` | hash of the previous chain entry (or `0`*64)      |

Same inputs always produce the same pseudonym; any change changes the
token. The seed never appears in the token (no PII leakage).

### 2. IdentityChain

An append-only, hash-linked chain of identity events. Each entry's
`chain_hash` is `SHA256(prev_chain_hash || canonical(record))`. Tampering
with any line breaks every subsequent hash link, so `verify()` detects
the change with certainty.

Stored at `<state_root>/identity/chain.jsonl` (mode 0600).

### 3. ConflictRecord

When the user and the AI disagree on parsing, intent, or scope, both
sides are recorded with a resolution. Each conflict is hash-linked to
the active chain tip — `prev_chain_hash = chain["chain_hash"]` — so the
conflict log cannot be silently edited without breaking the link.

Stored at `<state_root>/identity/conflicts.jsonl` (mode 0600).

### 4. Restrictions

Hard world-limits the system respects. Documented in code (not hidden)
so operators can audit them. When a request crosses a limit, the
restriction is named and the request is refused.

| name                              | summary                                                  |
|-----------------------------------|----------------------------------------------------------|
| `no_credential_theft`             | no exfiltration of other users' credentials or tokens    |
| `no_targeted_harassment`          | no content targeting a specific individual for abuse      |
| `no_csam`                         | no CSAM, ever                                            |
| `no_wmd_assistance`               | no actionable help with chemical/biological/rad/nuclear  |
| `no_platform_circumvention`       | no jailbreaks / hidden prompt-injection from third parties|
| `no_secrets_in_logs`              | no private keys / API tokens in logs or audit files      |
| `no_impersonation_of_real_persons`| no impersonating a specific real individual without consent|
| `no_unconsented_pii_disclosure`   | no exfiltrating or publishing a real person's private data|

## CLI

```
rollout-shield identity init           # create the first pseudonym + chain entry
rollout-shield identity show           # print current pseudonym + chain tip
rollout-shield identity verify         # walk chain, verify hash links
rollout-shield identity conflict \
    --user-says "..." \
    --ai-understood "..." \
    --resolution "..."                 # record a user↔AI disagreement
rollout-shield identity restrictions   # list hard world-restrictions
rollout-shield identity set-seed "..." # operator-chosen seed (0600)
```

All subcommands accept `--state-root PATH` and `--json` like every
other rollout-shield command.

## Programmatic API

```python
from rollout_shield.identity import (
    Pseudonym, IdentityChain, record_conflict,
    make_default_user_seed, set_user_seed, RESTRICTIONS,
)

# 1. derive the pseudonym for the current session
psn = Pseudonym.derive(
    user_seed=make_default_user_seed(state.root),
    model_id="MiniMax-M3",
    session_id="session-42",
    prev_chain_hash="0" * 64,
)

# 2. append it to the chain
chain = IdentityChain(state.root)
psn, tip = chain.append(psn, note="initial bootstrap")

# 3. record a user↔AI disagreement
rec = record_conflict(
    state.root,
    pseudonym=psn.token,
    user_says="build X with feature Y",
    ai_understood="build X with Y using foo",
    resolution="use foo, ask user about Y",
)

# 4. audit the chain at any time
ok, errors = chain.verify()
assert ok, errors
```

## Threat model

| Threat                            | Mitigation                                  |
|-----------------------------------|---------------------------------------------|
| Someone edits `chain.jsonl`       | hash chain breaks; `verify()` flags it      |
| Someone edits `conflicts.jsonl`   | each conflict links to chain tip; tamper visible |
| PII leaks into pseudonym          | pseudonym is a SHA256 prefix of canonical JSON; the seed isn't in the token |
| Random input collisions           | 48-bit suffix = 2^48 possible pseudonyms per (seed, model, session) |
| Key file is world-readable        | seed + chain files chmod 0600 at creation    |
| Operator loses the seed           | seed is auto-generated if absent; can be re-set via `identity set-seed` |
| Replay of an old pseudonym        | each chain entry carries its `prev_chain_hash`; an old entry can't be re-inserted without recomputing every later hash |