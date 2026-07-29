# Security Policy

## supported versions

| version | supported           |
|---------|---------------------|
| 0.1.x   | :white_check_mark:  |
| < 0.1   | :x:                 |

## reporting a vulnerability

**Please do not file public issues for security problems.**

Send a private report to the maintainers via GitHub's
[private vulnerability disclosure](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
form (Security tab → "Report a vulnerability").

You can also email the maintainer directly if the GitHub channel is
unavailable. **Do not** include exploit details in the subject line.

We aim to acknowledge reports within 48 hours and to ship a fix
within 14 days for critical issues, 30 days for high/medium.

## what to expect

1. **Acknowledgement** — we confirm receipt within 48h.
2. **Triage** — we assess severity (critical / high / medium / low)
   and reach back with our assessment.
3. **Patch** — we open a private security advisory branch and prepare
   a fix.
4. **Disclosure** — once the fix ships in a release, we publish a
   [GitHub Security Advisory](https://github.com/<org>/<repo>/security/advisories)
   with full credit to the reporter (unless they prefer anonymity).

## out of scope

The following are **not** considered vulnerabilities in this project:

- Self-hosted deployments where the operator has root on the host
  (the runtime assumes a non-malicious host — see `docs/SECURITY.md`).
- Key material held in `keys_material/` (this is local user state;
  production deployments must use hardware-anchored keys).
- Log injection via crafted claim bodies (the runtime treats claim
  bodies as opaque user input — operators must sanitize at the UI).

## threat model summary

See `docs/SECURITY.md` for the full model. Short version:

- We protect **claim authenticity + immutability + state integrity**.
- We do **not** protect against a malicious host or against the local
  user holding their own private key.

## hardening checklist

For production deployments (`controller_policy=device-only`):

- [ ] Hardware-anchored keys (TPM / HSM), no soft keys in
      `keys_material/`.
- [ ] `controller_policy=device-only` enforced
      (`rollout-shield space set-policy device-only --yes`).
- [ ] Daemon runs as a dedicated service user, not as root.
- [ ] `keys_material/` mounted from a read-only volume / Secret.
- [ ] Dashboard bound to `127.0.0.1` (operator opens via reverse proxy
      if remote access is required).
- [ ] `alert_webhook_url` empty by default; if set, treat as a secret.
- [ ] Logs ship to an external aggregator; log rotation enabled.
- [ ] `pre-commit install` runs locally on every commit
      (`.pre-commit-config.yaml` includes `detect-private-key`).