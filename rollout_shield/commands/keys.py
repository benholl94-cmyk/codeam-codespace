"""Key management commands.

Generates Ed25519 keypairs (PEM-encoded) and registers them with the
rollout-shield state. The private key file is written under the
state root (NOT the keys/ metadata directory) so it can be excluded
from backups independently of the metadata.

In production, key material is sealed in TPM/HSM (see
``hardware/tpm-key-storage.md`` and ``hardware/hsm-integration.md``).
For local development, this command generates a soft key that is
suitable for testing but should be replaced with a hardware-anchored
identity before any production signing.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import uuid
from pathlib import Path

from ..state import State, atomic_write_json


KEYS_MATERIAL_DIRNAME = "keys_material"


def _generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair via Python stdlib.

    Returns ``(private_pem, public_pem)``. The private key is written
    to a separate ``keys_material/`` directory and chmod'd to 0600.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, NoEncryption, PrivateFormat, PublicFormat,
        )
    except ImportError as exc:
        raise RuntimeError(
            "The `cryptography` package is required for key generation. "
            "Install with: pip install cryptography  (or use a hardware-anchored key)"
        ) from exc

    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    return priv_pem, pub_pem


def cmd_keys_new(state: State, agent_id: str, description: str = "") -> str:
    """Generate a new Ed25519 keypair and register it with state.

    Returns the new key id.
    """
    priv_pem, pub_pem = _generate_keypair()
    key_id = f"agk_{agent_id}_{uuid.uuid4().hex[:8]}"
    fingerprint = _fingerprint_pubkey(pub_pem)

    # private material lives in a separate, restrictive directory
    material_dir = state.root / KEYS_MATERIAL_DIRNAME
    material_dir.mkdir(parents=True, exist_ok=True)
    priv_path = material_dir / f"{key_id}.pem"
    priv_path.write_text(priv_pem)
    try:
        os_chmod_private(priv_path)
    except OSError:
        pass  # non-POSIX; permission hint only

    meta = {
        "id": key_id,
        "agent_id": agent_id,
        "description": description,
        "algorithm": "Ed25519",
        "public_key_pem": pub_pem,
        "fingerprint": fingerprint,
        "private_key_path": str(priv_path),
        "created_at": int(time.time()),
        "hardware_anchored": False,
    }
    state.put_key(key_id, meta)
    return key_id


def os_chmod_private(path: Path) -> None:
    import os
    os.chmod(path, 0o600)


def _fingerprint_pubkey(pub_pem: str) -> str:
    import hashlib
    digest = hashlib.sha256(pub_pem.encode("ascii")).digest()
    return "sha256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def cmd_keys(state: State, args: argparse.Namespace) -> int:
    sub = args.keys_command or "list"
    if sub == "list":
        return _keys_list(state, args)
    if sub == "new":
        key_id = cmd_keys_new(state,
                              agent_id=args.agent_id,
                              description=args.description)
        print(f"created key: {key_id}")
        keys = state.list_keys()
        for k in keys:
            if k.get("id") == key_id:
                print(f"  agent_id:    {k.get('agent_id')}")
                print(f"  fingerprint: {k.get('fingerprint')}")
                print(f"  algorithm:   {k.get('algorithm')}")
                print(f"  material:    {k.get('private_key_path')}")
        return 0
    if sub == "show":
        meta = state.get_key(args.key_id)
        if meta is None:
            print(f"no such key: {args.key_id}", file=__import__("sys").stderr)
            return 1
        sanitized = {k: v for k, v in meta.items()
                     if k not in ("private_key_pem", "private_key")}
        if args.json:
            print(json.dumps(sanitized, indent=2, sort_keys=True))
        else:
            for k, v in sanitized.items():
                print(f"  {k}: {v}")
        return 0
    print(f"unknown keys subcommand: {sub}", file=__import__("sys").stderr)
    return 2


def _keys_list(state: State, args: argparse.Namespace) -> int:
    keys = state.list_keys()
    if args.json:
        sanitized = []
        for k in keys:
            sanitized.append({kk: vv for kk, vv in k.items()
                              if kk not in ("private_key_pem", "private_key")})
        print(json.dumps({"keys": sanitized}, indent=2))
        return 0
    if not keys:
        print("no keys registered. run: rollout-shield keys new --agent-id <id>")
        return 0
    print(f"{len(keys)} key(s) registered:")
    for k in keys:
        print(f"  - {k.get('id')}: agent={k.get('agent_id')} "
              f"fp={k.get('fingerprint')}")
    return 0
