# TPM 2.0 Key Storage Specification

> **Component**: TPM 2.0 (Trusted Platform Module) key-storage backend
> **Purpose**: Bind the agent's Ed25519 signing key to a specific
> hardware platform, so the key cannot be extracted even with full
> host compromise.
> **Status**: Specification v0.1
> **Audience**: DevOps engineers provisioning rollout-shield on
> production hosts.

## Why TPM 2.0?

TPM 2.0 is a discrete security chip present on most modern x86 servers,
laptops, and many industrial boards. It provides:

- **Sealed storage**: keys can be bound to Platform Configuration
  Registers (PCRs) that reflect the boot state. The key is released
  only when the PCRs match the values they had at sealing time.
- **Endorsement key**: a unique RSA key burned into the chip at
  manufacture, used to certify other keys.
- **Standardized interface**: TCG-standardized commands; libraries
  available across operating systems.

For `rollout-shield`, the TPM is used to wrap (encrypt) the agent's
Ed25519 private key with a TPM-resident storage key. The wrapped key
can be stored on disk; unwrapping requires the TPM.

## Threat addressed

Without TPM, the Ed25519 private key exists in host memory or on
host disk. An attacker with arbitrary code execution can:

1. Read the key from memory (`/proc/<pid>/mem` if same UID).
2. Read the key from disk if it is not encrypted.
3. Exfiltrate the key over the network.

With TPM-sealed storage:

1. The on-disk wrapped key is useless without the TPM.
2. Unwrapping requires the TPM to attest to specific PCR values
   (boot state).
3. Re-sealing to a different TPM (different machine) is impossible
   without the original TPM's endorsement key.

The remaining attack surface is: a compromised kernel that signs
arbitrary claims while pretending to be the legitimate agent. This
is mitigated by HSM (see `hsm-integration.md`) or by short-lived
TPM-issued keys that must be re-issued periodically.

## Specification

### Key generation

```
1. Agent (host) requests TPM to create a new RSA-2048 storage key
   under the platform's primary storage hierarchy.
   - Authorization: empty (well-known) for the platform hierarchy;
     or TPM2_RH_OWNER with the owner authorization value.
2. TPM returns a key handle; the public part is exported.
3. Agent generates an Ed25519 keypair in software (or via the
   TPM's HMAC/ECDAA facilities if Ed25519 is supported by the TPM
   firmware; otherwise, software-generated).
4. Agent wraps the Ed25519 private key with the TPM-resident RSA
   storage key using TPM2_RSAES.
5. The wrapped key is written to disk as
   `agent/identity.json.wrapped` (gitignored).
```

### Key usage (signing)

```
1. Agent reads the wrapped key from disk.
2. Agent asks TPM to unwrap (TPM2_RSAED) the Ed25519 private key,
   under the PCR values matching the boot state at provisioning
   time. PCR mismatch → unwrap fails → signing cannot proceed.
3. The unwrapped Ed25519 private key is held in TPM volatile memory
   for a short window (e.g., one claim's worth of signing).
4. Agent uses the private key to sign the claim.
5. Agent asks TPM to flush the volatile key (TPM2_FlushContext).
```

### Reference implementation

The reference implementation uses `tpm2-tools` (Linux):

```bash
# Create RSA storage parent under OWNER hierarchy
tpm2_createprimary -C o -G rsa2048 -c /tmp/parent.ctx

# Generate a child RSA key (used to wrap Ed25519)
tpm2_create -C /tmp/parent.ctx -G rsa2048 \
            -u /tmp/wrap.pub -r /tmp/wrap.priv -c /tmp/wrap.ctx

# Wrap an Ed25519 private key (32 bytes) — software-side
openssl genpkey -algorithm Ed25519 -out /tmp/ed25519.pem
ED25519_RAW=$(openssl pkey -in /tmp/ed25519.pem -outform DER | tail -c 32)

# Wrap the raw key with the TPM-resident RSA key
tpm2_rsaencrypt -c /tmp/wrap.ctx -o /tmp/ed25519.wrapped <(echo -n "$ED25519_RAW")
```

The reference implementation is approximately 80 lines of bash; a
production implementation in Go or Rust is on the v0.2 roadmap.

### Compatibility

| TPM chip | Status | Notes |
|---|---|---|
| Infineon TPM 2.0 (most x86 laptops/servers) | supported | Standard PCR layout, RSA-2048 |
| STMicro TPM 2.0 | supported | Some firmware versions disable RSA; check before provisioning |
| Microsoft Pluton | partial | TPM 2.0 spec-compliant; some commands restricted on consumer SKUs |
| Apple Secure Enclave | NOT supported | Different interface; not TPM 2.0 |
| Google Titan | NOT supported | Proprietary; no standard TPM commands |
| ARM TrustZone on Raspberry Pi | partial | Opt-in; needs firmware enabling |

### PCR policy

The default PCR policy binds the unwrap to the boot state at the
time of `tpm2_create`:

- PCR0: BIOS / firmware measurements
- PCR4: bootloader (GRUB, systemd-boot) measurements
- PCR7: Secure Boot state

Any of these PCRs changing (e.g., BIOS update, kernel upgrade)
requires re-sealing. This is intentional — a firmware-level attack
that modifies the boot chain cannot silently extract the key.

### Rotation

Keys are rotated by re-running the key-generation flow above with
a new parent key or a new PCR policy. The agent's
`public_keys[]` array in `agent/identity.json` retains the old
public keys with `valid_until` set, so old signatures remain
verifiable while new claims are signed with the new key.

### Failure modes

| Failure | Behavior |
|---|---|
| TPM not present | Signing fails; agent falls back to software-only keys with warning |
| TPM locked after too many failed auth attempts | Operator intervention required (TPM dictionary-attack lockout) |
| PCR mismatch on boot | Wrapped key cannot be unwrapped; agent cannot sign; rollout blocks until policy is re-sealed |
| Wrapped key on disk is corrupted | Re-run key-generation flow with the same endorsement key |
