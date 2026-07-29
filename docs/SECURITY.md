# Security & threat model

## what the runtime protects

- **Claim authenticity** — every claim is signed Ed25519 by the
  agent's registered key. The signature is over the canonical JSON
  preimage (RFC 8785 JCS-style).
- **Claim immutability** — the claim log is append-only; the JSONL
  files are written with `fsync` after each append.
- **State integrity** — every JSON write is atomic (temp + rename).
  A crash mid-write cannot corrupt state.
- **Single-authority control** — the `controller_policy` config
  field declares which keys are permitted to sign (device-only,
  human-only, or shared).

## what the runtime does NOT protect

- **The user's local shell.** The CLI runs as the user; if the user
  can read `~/.rollout-shield/keys_material/`, they can sign claims.
- **The local OS.** A compromised host can read the key material +
  state. The runtime assumes a non-malicious host.
- **The protocol semantics.** The runtime is a *tool*; the protocol
  is the *truth*. Anyone holding a private key can sign whatever
  they want. The controller policy is local enforcement.

## key handling

Production keys **must** be hardware-anchored. The runtime supports
this via the `--hardware-anchored` flag on `keys new`:

- Local dev: `cryptography`-generated soft keys
- Production: TPM/HSM-sealed keys, declared `hardware_anchored=True`
  but their private material never lives in `keys_material/`

`keys_material/` is **not** part of the repo and is **not** backable
up to a public location. The default permission mode is 0700; the
self-heal check fails if any other mode is set.

## controller policy

The `controller_policy` config field is the single source of truth
for "who is allowed to sign in this space". Three policies:

- `shared` (default) — human + device keys both permitted
- `device-only` — only hardware-anchored keys (the App-controlled Space)
- `human-only` — only non-hardware-anchored keys (dev/test)

Enforced at: `keys new`, `claim create`, every monitor cycle, every
self-heal cycle. Switching policy:

```bash
rollout-shield space set-policy device-only --yes
rollout-shield space validate
```

The `set-policy` command backs up the previous config to
`config.json.bak.<ts>` so a bad policy can be reverted.

## daemon surface

The daemon runs as a long-lived background process. It does NOT
expose a network port by default. The dashboard HTTP server is a
separate process that the operator starts explicitly
(`rollout-shield dashboard`). The default dashboard bind is
`127.0.0.1` — operator must opt into external exposure.

## alert dispatch

The daemon can dispatch alerts to a webhook. The URL is read from
`alert_webhook_url` in the state config. The default is empty (no
webhook). If you set a webhook, treat it as a secret.

## supply chain

- `pyproject.toml` declares optional dependencies (`crypto`, `dev`).
  The runtime itself is stdlib-only.
- The repo ships with `.pre-commit-config.yaml` (ruff + mypy +
  detect-private-key) to catch leaked PEMs before commit.
- CI workflows validate CLAUDE.md Beads markers, run lint, run
  smoke tests, and post benchmark snapshots.

## reporting

Security issues: open a GitHub issue labelled `security` (or follow
the project's private disclosure process if one is set up).
