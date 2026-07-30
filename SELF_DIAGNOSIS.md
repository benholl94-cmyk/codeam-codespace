# Self-Diagnosis — Interactive Mode

> Conversation with self, finding blind points, chaining tech & economics
> forward, building workarounds for any pop-off that can break the work.

---

## A. Repo Full-Build (the lay of the land)

Five layers, built in this order, each tested before the next:

```
Layer 5: AUTONOMIE          .claude/skills/autonomie/   SKILL.md + 4 scripts
Layer 4: ZERO-LEAK          tools/{audit_log,secure_state,owner_log,leak_check,privacy_audit}.py
                              + SECURITY.md, .gitignore hardening, .env.example
Layer 3: HEALTH/RELEASE     tools/{doctor,release}.py    10-check probe + semver pipeline
Layer 2: SAFEUP             tools/safeup.py              rotating snapshot + preop rollback
Layer 1: CORE               rollout_shield/              CLI + state + signing + HTTP
Layer 0: BEADS              .beads/                      task tracking + persistent memory
```

Tests: 30/30 pass. Privacy audit: PASS. Audit chain: clean.
Push: 5 commits on `main`, latest `f4331d8`.

---

## B. Interactive Q&A — Self-Diagnosis

> Mode: I ask a question, I answer it, the answer surfaces a blind point
> that becomes the next question. Looping until I dry out.

---

**Q1. What did we actually build that runs without my hand?**

A1. Three closed-loop systems:
- **Safeup loop**: snapshot → mutate → verify → restore-on-fail
- **Audit loop**: action → append → hash-chain → verify-on-demand
- **Health loop**: doctor checks → fail counts → handoff

Each is independently runnable. Each has a `verify` that proves integrity.

**Blind point surfaced:** The loops are isolated. They don't cross-verify each
other. A corrupt safeup would only be caught if someone *runs* `safeup verify`;
an unrotated audit log is silent. There's no master heartbeat.

---

**Q2. Where is the master heartbeat missing?**

A2. There isn't one. `doctor.py` runs 10 checks but doesn't check
`.owner_unlock` presence, doesn't check audit log size, doesn't check
safeup index for corruption. The owner must remember to run
`privacy_audit.sh` separately.

**Blind point surfaced:** `doctor.py` and `privacy_audit.sh` overlap but
neither subsumes the other. The owner has to know which to run when.

---

**Q3. What did I never write tests for?**

A3. The security layer (zero-leak). I tested `safeup.py` (6 tests),
`state.py` (11 tests incl. migration), `claim.py`, `lock.py`,
`reputation.py`, `git_safe.py`, `smoke.py`. But:
- `audit_log.py` — no unit tests
- `secure_state.py` — no unit tests
- `owner_log.py` — no unit tests
- `leak_check.sh` — no test that false positives are zero
- `privacy_audit.sh` — only manual verification

**Blind point surfaced:** Security code is untested. A regression in
`audit_log.append()` that breaks the chain wouldn't be caught.

---

**Q4. What single point of failure would lose the most data?**

A4. `.audit/.owner_unlock`. If the owner loses it, every Fernet-encrypted
state file is unrecoverable. There's no backup mechanism. No key escrow.
No recovery phrase. No multi-key split.

This is the **highest-impact single point of failure** in the whole
architecture.

**Blind point surfaced:** No backup/recovery flow for the owner key.

---

**Q5. What's the most likely way an external system reads our data?**

A5. Not via our code. Via:
1. Cloud backup syncing `.rollout-shield/` or `.audit/` plaintext
   (mitigated by Fernet but not eliminated)
2. The alerter webhook — user-configured URL, single POST egress
3. `git log -p` showing historical unencrypted state if someone
   committed `.rollout-shield/` before gitignore was hardened
4. Core dumps / swap / hibernation files containing in-memory plaintext
5. Process listings showing argv with `--webhook-url=https://secret/...`

**Blind point surfaced:** The alerter is the only HTTP egress but it's
ungated. There's no policy: "webhook URLs must be HTTPS to a specific
allowlist", or "POST bodies must be redacted of certain fields".

---

**Q6. What would break this under load?**

A6. Three things:
1. **Audit log unbounded growth**: append-only, no rotation. After 1M
   entries at ~500 bytes = ~500 MB. Read performance on `tail N` is O(N)
   on disk; on 1M-line files = ~2s latency.
2. **Snapshot tarball on huge repos**: `_tar_dir` walks `rglob` and
   tarfile-compresses serially. A 50K-file repo takes minutes and
   produces a multi-GB tarball.
3. **Doctor rerunning 10 checks on every commit**: ~3s; fine for local,
   painful in tight pre-commit loops.

**Blind point surfaced:** No resource caps. A misbehaving script could
fill the disk by spamming snapshots or audit entries.

---

**Q7. What's silently degrading?**

A7. The `.audit/` permissions. We chmod 0700 on creation, but if the
operator runs the audit tools from a different uid, or if a system
backup restores mode 0755, the protection is gone. There's no periodic
permission check.

The `.owner_unlock` is mode 0600, but `secure_state.py --init` only
chmods on create. If the file is touched/edited by another tool, mode
could regress silently.

**Blind point surfaced:** No integrity monitoring on the security
infrastructure's own permissions.

---

**Q8. What can I do that the owner can't see?**

A8. Right now, anything I do gets an audit entry. But:
- If `audit_log.py` import fails (path issue), my actions are silent
- If `_audit()` is in a code path that raises before the append, the
  action is unaudited
- The audit append is best-effort with `try/except: pass` — failures
  are invisible

The owner sees "everything worked" only because the chain verifies.
If audit silently broke, the owner would still see a valid chain
(stale).

**Blind point surfaced:** No heartbeat proving the audit log is being
written to. A silent audit failure is undetectable from inside.

---

**Q9. What did I assume that might not hold?**

A9. Five assumptions:
1. **Stdlib never phones home**: true today, no guarantee tomorrow
2. **`cryptography` is benign**: it's a well-known lib, but any
   dependency is a supply-chain risk
3. **GitHub doesn't index encrypted state**: `.rollout-shield/` is
   gitignored, so GitHub never sees it. BUT if the owner pushes before
   gitignore is in place, history retains the file (until rewritten)
4. **Container/VM is sandboxed from host**: the Claude runtime
   probably shares /tmp and possibly other paths with the host
5. **The owner reads the audit log regularly**: humans don't.

**Blind point surfaced:** The biggest one — assumption #5. The
audit log is built but the owner might never check it. A weekly
`tools/owner_log.py summary 7` reminder is missing.

---

**Q10. What would I do if I were the attacker?**

A10. Top 5 attack vectors I would try:
1. **Steal `.owner_unlock`**: if the file leaks (cloud backup, ls
   mistake, process inspection), I have every encrypted file
2. **Inject into the audit log**: but the hash chain catches this —
   this is actually defended
3. **Modify `doctor.py` / `safeup.py` to skip `_audit()` calls**:
   silent gap in the audit coverage. The chain looks fine but
   doesn't represent reality.
4. **Replace `.audit/audit.jsonl` with a pre-computed chain**: this
   works only if the attacker knows all future entries, which is
   hard. But feasible for short time windows.
5. **Race the write_lock**: `state.py` uses fcntl flock which is
   correct on POSIX, but on Windows it's a no-op. Cross-platform
   is a footgun.

**Blind point surfaced:** #3 is real — there's no verification that
the audit append was actually called from the expected code path. A
modified `_audit()` function that does nothing would not be caught.

---

**Q11. Where am I the weakest — honestly?**

A11. The human factors:
- The owner might not run the audit
- The owner might lose the unlock file
- The owner might commit `.env` by accident before gitignore applies
- The owner might bind the HTTP server to 0.0.0.0 (currently 127.0.0.1)
- The owner might configure a webhook that exfiltrates to attacker

I can build tools. I cannot make the operator use them.

---

## C. Tech & Economics Chain (loopback into new world)

> Each Q spawns an A which spawns a new Q. The chain runs until the
> questions stop being useful.

---

**Q1. What's the actual cost of "zero-leak" in 2026?**

A1. Per op:
- Fernet encrypt: ~5 μs (single AES-128-CBC + HMAC)
- File write: 1 fsync ≈ 1-10 ms (disk-bound)
- Audit append: 1 fsync ≈ 1-10 ms + sha256 ≈ 50 μs
- Total per state write: ~5-20 ms wall-clock

For 1K state writes/sec: ~5-20% CPU on a single core. Negligible for
humans, expensive for tight loops.

**Q2. What cost model breaks this?**

A2. Sustained write rates > 10K/sec saturate the fsync. SSD fsync is
~100 μs, HDD ~10 ms. The audit log becomes the bottleneck.

Workaround: batch audit appends (every 100ms, flush a buffer).
Trade-off: lose the strict per-action audit, gain 10-100× throughput.

**Q3. Where does the cost model flip — i.e. when does zero-leak cost less than not?**

A3. Compliance regimes. GDPR fines averaged €2.92M per breach in 2024.
HIPAA settlements average $1.5M per incident. SOC2 Type II requires
demonstrable audit trails.

If your "audit-able zero-leak posture" saves one breach investigation,
the crypto + storage cost is recouped in the first incident.

**Q4. What's the next architectural shift after "encrypted at rest"?**

A4. **Encrypted in use**: confidential computing (AMD SEV-SNP, Intel
TDX, ARM CCA). Data stays encrypted in RAM during computation.
Attestation proves the enclave is unmodified.

In 2026, Nitro Enclaves on AWS, Azure Confidential VMs, GCP Confidential
VMs all offer this. The Python ecosystem hasn't caught up — `cryptography`
doesn't yet support enclave-sealed keys — but the runtime is arriving.

**Q5. What does this mean for agent architectures like Claude?**

A5. Three implications:
- Agent memory (state) can be sealed in an enclave that even the
  operator's host OS can't read
- Agent-to-agent signing becomes a primitive — every claim has a
  verifiable provenance trail
- Multi-agent swarms can compose trust chains: agent A trusts agent
  B because B's enclave attestation matches B's published public key

**Q6. What about the economics — who pays for the compute?**

A6. Today's reality: cloud providers charge 2-4× for confidential
compute. 2027 prediction: prices collapse as AMD/Intel/ARM push
hardware enclaves as default, not premium.

Workaround now: selectively seal the *most* sensitive state (signing
keys, owner tokens) even if general state is plaintext. Defense in
depth without the 4× cost.

**Q7. How does this change the agent / owner relationship?**

A7. From "operator trusts the cloud" to "operator cryptographically
constrains the agent". The owner publishes a public key; the agent
attests that its actions are signed under the operator's key. The
operator verifies offline.

This is the *thesis* of this repo: trust through verifiable evidence,
not through trust-the-cloud-by-default.

**Q8. What's the next 3 years likely to bring?**

A8. (speculative, calibrated)
- 2026: SEV-SNP/TDX become standard in cloud SKUs. Python enclaves via
  `pysec`/`encrypted-compute` libs.
- 2027: Agent attestation standards (IETF draft on Agent Credential
  Format). Cross-vendor signature verification.
- 2028: Differential privacy + federated learning converge with agent
  architectures. Agents can prove useful work without seeing raw data.

**Q9. Where does this repo sit in that arc?**

A9. 2024-style: encrypted at rest, hash-chained audit, owner-controlled
unlock, no telemetry, stdlib-only. This is the **prerequisite layer**.
Enclaves (2026+) will strengthen the "in use" layer; the rest of the
architecture composes with that.

**Q10. What do we lose if we don't move up the arc?**

A10. We stay competitive for now (compliance + portability win) but
lose the next-gen differentiator: verifiable agent compute. An
attacker with $10M and a side channel can read our RAM. Confidential
compute closes that gap. Without it, our ceiling is "well-configured
traditional security."

---

## D. Pop-Off Matrix — Failure Modes + Workarounds

> Every failure mode that can break the work, with a workaround I can
> ship today (or soon). Items are graded by impact × likelihood.

| # | Pop-off | Impact | Likelihood | Workaround |
|---|---|---|---|---|
| 1 | Owner loses `.owner_unlock` | CRITICAL | medium | Add `secure_state.py --backup` writing a paper-printable recovery sheet (key as 24 BIP-39 words) |
| 2 | Audit log grows unbounded | medium | high | Add `audit_log.py --rotate` (archive, start new chain with new genesis) |
| 3 | Audit silent failure (broken `_audit()`) | high | medium | Add a heartbeat: `audit_log.py --heartbeat` writes a daily entry; `privacy_audit.sh` checks last-entry age |
| 4 | Modified `_audit()` in safeup.py / doctor.py | high | low | Subresource integrity: `safeup.py` and `doctor.py` self-hash on startup; refuse if hash mismatch |
| 5 | Webhook URL typo leaks data | medium | medium | Add webhook allowlist (env var) + dry-run mode for alerter |
| 6 | HTTP server bound to public interface | high | low | Default-bind 127.0.0.1 already; add `--public-bind-require-confirm` flag |
| 7 | Cloud backup syncs `.audit/` plaintext | high | medium | Already Fernet-encrypted; add `secure_state.py --audit-dir` test that confirms files are Fernet tokens |
| 8 | `.gitignore` regressed, secrets committed | CRITICAL | low | Add pre-commit hook: `leak_check.sh` blocks commits with leaks; `git log -p -- .env` reveals history |
| 9 | GitHub Action exfiltrates via `curl -d $SECRET` | high | medium | `leak_check.sh [8]` already flags; add a CI-only run with `set -e` |
| 10 | Owner never runs `owner_log.py` | medium | high | Add a SessionStart hook that prints "[autonomie] run `tools/owner_log.py summary` weekly" |
| 11 | Safeup snapshot fills disk | medium | medium | Already pruned to KEEP=10; add `--max-bytes` cap |
| 12 | Doctor rerun is slow in pre-commit | low | high | Cache doctor results; skip when `git diff --quiet` |
| 13 | Audit chain fork (two appenders race) | medium | low | Audit append already uses O_APPEND + fsync; add a per-process nonce to detect forks |
| 14 | fsync unavailable on some FS (NFS, FUSE) | medium | medium | Wrap fsync in try/except; warn instead of fail |
| 15 | Windows fcntl no-op means no write lock | high | medium (windows-only) | Document: rollout-shield is POSIX-first; Windows is best-effort single-process |
| 16 | cryptography lib supply-chain attack | CRITICAL | very low | Pin version in setup.sh; `pip install --require-hashes` for CI |
| 17 | Alerter URL becomes attacker-controlled | high | low | Webhook signature: HMAC the body with shared secret; receiver verifies |
| 18 | Owner re-installs OS, loses unlock | CRITICAL | low | Backup flow (see #1) + periodic `secure_state.py --remind-backup` |
| 19 | Beads auto-export includes a secret | medium | low | Beads content is local-only; but add a hook to redact `secret`/`token` keys before export |
| 20 | Container snapshot includes decrypted RAM | high | low | Document: don't take snapshots mid-state-write; safeup-before-snapshot already covers disk state |
| 21 | Python 3.8 compatibility breaks (cryptography drops 3.8) | medium | medium | Pin Python ≥3.10 in setup.sh; document migration to 3.11+ |
| 22 | New third-party dep slips in via copy-paste | medium | medium | `leak_check.sh [4]` already catches; add CI run with `--fail-on-any-warn` |
| 23 | Owner mis-configures audit dir to a world-writable path | medium | low | `audit_log.append()` chmods to 0700 on every append; warn if chmod fails |
| 24 | Audit log replayed from old chain | medium | low | Add monotonic counter in detail; verify monotonicity in `owner_log verify` |

---

## E. Autonomy Extension — Next Concrete Steps

What I'll build next (in priority order, all `safeup`-first):

1. **`tools/owner_log.py heart`** — heartbeat entry every 24h, age-checked
2. **`tools/secure_state.py --backup`** — printable 24-word recovery sheet
3. **`tools/safeup.py` and `tools/doctor.py` self-hash on startup** — refuse
   to run if their hash doesn't match the recorded baseline
4. **`tools/audit_log.py --rotate`** — archive current chain, start new
   genesis, keep N archived chains
5. **Web dashboard hardening** — explicit `--bind 127.0.0.1` default,
   refuse `--bind 0.0.0.0` without `--i-know-this-is-public`
6. **Pre-commit hook for `leak_check.sh`** — block any commit that
   introduces a leak
7. **Recovery flow** — `secure_state.py --recover <24-words>` rebuilds
   the unlock from a paper backup

These seven items close the highest-impact gaps from the blind-point
list. None require third-party deps. All work with existing infra.

---

## F. Self-Honest Closing Note

The strongest thing this repo has is **composability**: every layer
was built to plug into the next without modification. The weakest
thing is **operator discipline** — humans forget, mis-configure,
lose files. Tools can surface the risk; they cannot eliminate it.

The right framing: this is a *defense-in-depth* posture that raises
the cost of attack from "easy" to "expensive" — not a guarantee.
