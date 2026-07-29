# HSM Integration Specification

> **Component**: Hardware Security Module (HSM) integration via PKCS#11
> **Purpose**: Hold the agent's Ed25519 signing key in a tamper-resistant
> appliance so that the key never enters host memory.
> **Status**: Specification v0.1
> **Audience**: Security architects and DevOps engineers deploying
> rollout-shield at scale or in regulated environments.

## Why HSM?

A TPM seals a key to a single host. An HSM is a separate appliance
(physical or virtual) that holds keys for many hosts and never exposes
the private key material to any host. Use cases that warrant HSM:

- **Multi-host agents**: an agent identity that signs claims from
  many CI runners, all using the same key.
- **Compliance**: PCI-DSS, FIPS 140-2 Level 3+, SOC 2 Type II may
  require keys to be held in certified hardware.
- **High-value targets**: a single key signing thousands of claims
  per day is a high-value target for exfiltration; HSM raises the
  cost.

## PKCS#11 interface

The integration is via the PKCS#11 standard (Cryptoki). Most HSMs
expose a PKCS#11 module; clients link against `libpkcs11` or
`pkcs11-tool`. The reference implementation uses `pkcs11-tool` from
OpenSC.

### Key generation

```
1. Operator logs into the HSM (smartcard, password, or quorum auth).
2. Operator generates an Ed25519 keypair on the HSM with a known
   CKA_LABEL (e.g., "rollout-shield-agent-key-1").
3. HSM returns the public key (extractable) and the private key
   (non-extractable, by HSM policy).
4. Public key is published to agent/identity.json public_keys[].
5. Private key cannot be exported; signing must happen on the HSM.
```

### Signing

```
1. Host loads the PKCS#11 module for the HSM.
2. Host opens a session and logs in (C_Login) using operator credentials.
3. Host locates the Ed25519 private key by CKA_LABEL.
4. Host calls C_SignInit + C_Sign with the canonical claim body.
5. HSM returns the signature.
6. Host closes the session (C_Logout) and unloads the module.
```

The Ed25519 private key never leaves the HSM. The host sees only
the signature output.

### Reference implementation (YubiHSM 2)

YubiHSM 2 is a low-cost USB HSM with FIPS 140-2 Level 3 validation.
Reference flow using `yubihsm-shell`:

```bash
# Generate Ed25519 key on the HSM (one-time)
yubihsm-shell -p password --action generate-asymmetric-key \
    --key-id 0x1234 --algorithm ed25519 --capabilities sign-eddsa

# Sign a claim (per-operation)
echo -n "$CLAIM_CANONICAL" | yubihsm-shell -p password \
    --action sign-eddsa --key-id 0x1234 --in -
```

For production, integrate via PKCS#11 module (`yubihsm_pkcs11.so`)
so that standard PKCS#11 clients (OpenSSL, Java, Go) can sign without
learning the YubiHSM-specific protocol.

### Cloud HSMs

| Vendor | Product | PKCS#11 module |
|---|---|---|
| AWS | CloudHSM | `aws-cloudhsm-pkcs11` (via client SDK) |
| Azure | Key Vault Managed HSM | PKCS#11 v2.40 (preview) |
| Google | Cloud HSM | Custom (gRPC); no PKCS#11 directly |
| Thales | Luna Network HSM | Standard PKCS#11 |
| Entrust | nShield | Standard PKCS#11 |

For Google Cloud, use the gRPC client directly or wrap it in a
local PKCS#11 shim (community implementations exist).

### Authentication to the HSM

| Auth method | Use case |
|---|---|
| Single operator password | Small teams, low-frequency signing |
| Smartcard (PIV) | Mid-size teams; requires operator presence |
| Quorum (M-of-N) | High-value signing keys (e.g., 3-of-5 operator quorum) |
| Remote attestation | Cloud HSMs; attestation token verifies the HSM firmware |

For `rollout-shield` agent keys, single-operator password is the
default. Quorum auth is appropriate for human-attributable signing
(e.g., a human supervisor signing off on a production rollout).

### Key rotation

Same as TPM: append a new key entry to `agent/identity.json`
`public_keys[]`, set `valid_until` on the old key, generate the new
key on the HSM with a new CKA_LABEL.

### Failure modes

| Failure | Behavior |
|---|---|
| HSM unreachable | Signing fails; agent falls back to in-memory software key with warning (if policy allows) |
| HSM operator auth fails | Lockout after N attempts; operator must reset |
| HSM firmware update required | Plan a maintenance window; re-authenticate |
| HSM clock skew | Claims timestamps may be off; verify system clock sync |

### Cost considerations

| HSM class | Approximate cost | Use case |
|---|---|---|
| USB HSM (YubiHSM 2) | ~$650 each | Per-host agent signing |
| Network HSM (Thales Luna) | $10k–50k | Shared, mid-scale |
| Cloud HSM (AWS CloudHSM) | ~$1.50/hour per cluster | Shared, elastic scale |
| FIPS 140-2 Level 3+ appliance | $50k+ | Regulated industries |

For most agent signing workloads, a single USB HSM or a small
cloud-HSM cluster is sufficient. The cost scales with the number
of distinct agent identities, not the number of signatures.
