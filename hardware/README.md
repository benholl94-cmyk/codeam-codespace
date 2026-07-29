# Hardware Layer

The hardware layer of `rollout-shield` is **advisory** — the software
layer runs end-to-end without any of the components described here.
The hardware layer *raises the trust ceiling* by rooting the agent's
signing key in tamper-resistant silicon and by providing physical
testbeds for rollout rehearsal.

This directory contains specifications, not hardware designs. The
specifications are vendor-neutral where possible; specific product
mentions are illustrative.

---

## Why hardware?

The Claims Protocol (`protocol/README.md`) signs every claim with an
Ed25519 private key. If the private key is stored in software
(plaintext on disk, in a `.env` file, in a process's memory), it is
exposed to:

- **Disk-theft attacks**: anyone with file-system access exfiltrates
  the key.
- **Memory-disclosure attacks**: a memory-dump vulnerability leaks
  the key.
- **Supply-chain attacks**: a malicious dependency reads the key.

Hardware mitigations:

| Threat | Software mitigation | Hardware mitigation |
|---|---|---|
| Disk theft | Encrypted-at-rest (LUKS, FileVault) | TPM 2.0 sealed key — key is bound to the platform |
| Memory disclosure | Memory-safe language, constant-time ops | HSM — key never enters host memory |
| Supply-chain | Code review, SBOM, reproducible builds | HSM — even malicious code cannot extract the key |

The hardware layer is therefore a defense-in-depth measure. It is
not required for the protocol to function, but it raises the cost
of key compromise from "free if you can run arbitrary code" to
"requires physical access to the device".

---

## Components

| Component | File | Status |
|---|---|---|
| TPM 2.0 key storage | `tpm-key-storage.md` | Spec draft |
| HSM integration | `hsm-integration.md` | Spec draft |
| Edge rollout testbed | `edge-rollout-testbed.md` | Concept |

---

## Compatibility matrix

| Component | Reference implementation | Tested platforms |
|---|---|---|
| TPM 2.0 | `tpm2-tss` (IBM), `go-tpm` (Google) | Linux + libtpm, modern x86 |
| HSM (PKCS#11) | `yubihsm-shell`, AWS CloudHSM, Azure Key Vault | Linux + pkcs11-tool |
| Edge testbed | Raspberry Pi 4, NVIDIA Jetson Orin | Linux ARM |

The hardware layer is intentionally built on standards (TPM 2.0,
PKCS#11) rather than vendor SDKs. This keeps the protocol portable
across hardware vendors and makes the spec auditable by third parties.

---

## Threat model

| Adversary | Capability | Mitigation |
|---|---|---|
| Remote code execution on host | Read disk, dump memory | Software: encrypted disk; Hardware: HSM (key never on host) |
| Physical access to host | Cold-boot attack, JTAG | Hardware: TPM sealed key (requires PCR values matching boot state) |
| Insider at cloud provider | Snapshot memory, read disk | Hardware: HSM in customer-managed key store (BYOK) |
| Compromised CI runner | Sign claims with attacker's key | Software: short-lived CI keys + hardware: HSM-rotated keys |

The hardware layer does not protect against all adversaries. It
specifically addresses the case where the host (devcontainer,
codespace, CI runner) is fully compromised but the attacker cannot
extract the HSM/TPM key.

---

## What is NOT in scope

The hardware layer does not address:

- **Side-channel attacks on signing operations** (power analysis,
  EM emanation). These require additional mitigations (constant-time
  implementations, Faraday cages) that are out of scope for v0.1.
- **Quantum attacks on Ed25519**. The protocol is not
  post-quantum-secure. A v0.2 roadmap item is to add a hybrid
  signature scheme (Ed25519 + Dilithium or similar).
- **Hardware backdoors**. We assume the TPM/HSM vendor is honest.
  Trust in the vendor is established by procurement review, not
  by the protocol.
