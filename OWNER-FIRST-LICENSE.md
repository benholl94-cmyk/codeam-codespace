# OWNER-FIRST LICENSE — rollout-shield

Version 1.0, 2026

This license applies to the software in this repository (the "Work").
It supplements and clarifies the proprietary terms in `LICENSE` by
making explicit what the **owner** of a deployment may do, what
**third parties** may not do, and what the Work **never does** to the
owner's data.

## Definitions

* **Owner** — the natural person who controls the hardware on which
  the Work is built and run, and whose git-committer identity is
  recorded in this repository's local configuration at the time of
  first install. The Owner's authority over the deployment is
  absolute and non-revocable.
* **Work** — the source code, documentation, protocol specifications,
  schema files, agent identity cards, scripts, tools, generated
  deployment bundles, and any artifact produced by tools contained
  in this repository.
* **Deployment** — any installation, execution, or invocation of the
  Work on hardware the Owner controls.

## Section 1 — Owner's rights (unrestricted)

The Owner may, without further permission from anyone:

1. Use, execute, and run the Work on any hardware they control.
2. Modify, adapt, translate, fork, and rebase the Work for their own
   use.
3. Distribute the Work (modified or unmodified) to hardware they
   control, including via private channels, USB, internal networks,
   and signed APK sideload.
4. Build derivative Works for their own use, including private
   commercial deployment.
5. Charge third parties for access to a Deployment they operate,
   provided that Section 2 is honored.
6. Inspect, read, modify, and delete any state, log, configuration,
   or artifact produced by a Deployment they operate.
7. Revoke any third party's access to a Deployment they operate, at
   any time, for any reason.
8. Re-license the Work (in whole or in part) under different terms
   for their own Deployments, provided that re-licensing does not
   extend those different terms to other Owners' Deployments.

## Section 2 — Third-party prohibitions

Without the Owner's prior, explicit, written consent, no third party
may:

1. **Receive telemetry, analytics, logs, state, claims, reputation
   data, audit entries, configuration, or any other artifact** from
   a Deployment, by any means (network, file system, side channel,
   optical, acoustic, or electromagnetic). The Work does not
   transmit these artifacts; third parties may not add code, modify
   the Work, or use side channels to obtain them.
2. Modify the Work to transmit, exfiltrate, or expose Owner data to
   any destination the Owner has not explicitly whitelisted.
3. Re-identify, deanonymize, correlate, or aggregate Owner data
   with data from other sources.
4. Circumvent the Owner-unlock gate, the loopback bind, or any
   other safety mechanism in the Work.
5. Bundle the Work with telemetry SDKs, crash reporters, analytics
   services, or any code that contacts a network destination without
   the Owner's explicit per-destination allowlist.
6. Hold the Owner liable for damages arising from the Owner's use
   of the Work, except to the extent prohibited by applicable law.

## Section 3 — What the Work never does (architectural guarantees)

The Work's design contract with the Owner includes the following
architectural guarantees. The Owner may rely on them:

1. **No outbound network calls** — The Work never initiates a
   connection to a host the Owner has not explicitly configured.
   This is enforced by: (a) the absence of third-party networking
   dependencies; (b) the bind-only-loopback default for any
   embedded HTTP server; (c) the explicit refusal of public binds
   without an acknowledgment flag; (d) the Android variant's
   absence of the `android.permission.INTERNET` declaration.
2. **No telemetry** — The Work contains the verified absence of
   analytics code, tracking, crash reporting, or fingerprinting.
   Verified by `tools/leak_check.sh`.
3. **Encrypted at rest** — Sensitive state files (configuration,
   reputation, claim signing blocks) are encrypted with Fernet
   (AES-128 in CBC mode + HMAC-SHA256). The decryption key is
   owned exclusively by the Owner and stored with mode 0600.
4. **Tamper-evident audit log** — Owner-visible actions are recorded
   in a hash-chained append-only log (`audit.jsonl`). Any
   modification invalidates the chain and is detectable by
   `tools/audit_log.py --verify`.
5. **Loopback by default** — Any web server bundled with the Work
   binds to `127.0.0.1` (IPv4 loopback) by default and refuses
   binds to `0.0.0.0`, `::`, or empty strings without an
   explicit `--i-know-bind-is-public` acknowledgment.
6. **Owner-unlock gating** — Sensitive operations (reading or
   writing encrypted state, decrypting sealed backups) refuse to
   proceed when the Owner unlock file is absent.

These guarantees are **part of the Work**. A modification that
removes or weakens any of them is not a "modification" under
Section 1.2 — it is a fork that loses the OWNER-FIRST LICENSE and
must not bear the rollout-shield name or claim compatibility.

## Section 4 — No warranty

THE WORK IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
NONINFRINGEMENT. THE OWNER ASSUMES ALL RISK OF USE. THE AUTHOR(S)
SHALL NOT BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM,
OUT OF, OR IN CONNECTION WITH THE WORK OR THE USE OR OTHER
DEALINGS IN THE WORK.

## Section 5 — Termination

This license terminates automatically for any party that violates
Section 2. Upon termination, that party must cease all use of, and
destroy all copies of, the Work in their possession. Termination
of one party's rights does not affect any other party's rights.

## Section 6 — Governing law

This license is governed by the laws of the jurisdiction in which
the Owner resides. Any dispute is resolved in that jurisdiction's
courts, at the Owner's election.

## Section 7 — Entire agreement

This document, together with the `LICENSE` file and the
`COPYRIGHT.md` statement, constitutes the entire agreement between
the Owner and any other party with respect to the Work. No oral or
implied terms apply.

---

## How to apply this license

By cloning, building, or running this Work, you accept this license.
If you do not accept, do not use the Work.

The Owner of any Deployment is identified by the git committer
identity in the local repository configuration at first install.
This identity is recorded in `COPYRIGHT.md` and may not be reassigned
without a signed, dated instrument from the current Owner.