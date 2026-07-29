"""Claim management commands.

Claims follow the format in ``protocol/CLAIM-FORMAT.md``. Each claim
is signed by an agent key (Ed25519). For local development, signing
uses the soft keypair registered via ``keys new``. In production,
signing uses a key sealed in TPM/HSM (see ``hardware/``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
import uuid
from pathlib import Path

from ..state import State


VALID_TYPES = {"intent", "change", "test", "verify", "contradict", "delegate"}


def _sign_with_key(state: State, agent_id: str, payload_bytes: bytes) -> tuple[str, str, str]:
    """Sign the payload with the most recently created key for this agent.

    Returns ``(signature_b64, public_pem, key_id)``. Raises if no key
    exists for the given agent.
    """
    keys = [k for k in state.list_keys() if k.get("agent_id") == agent_id]
    if not keys:
        raise RuntimeError(
            f"no key registered for agent_id={agent_id}; "
            "run: rollout-shield keys new --agent-id " + agent_id
        )
    keys.sort(key=lambda k: k.get("created_at", 0), reverse=True)
    key = keys[0]
    priv_path = Path(key.get("private_key_path", ""))
    if not priv_path.exists():
        raise RuntimeError(f"private key material missing: {priv_path}")
    priv_pem = priv_path.read_text()

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError(
            "The `cryptography` package is required for signing. "
            "Install with: pip install cryptography"
        ) from exc

    priv = load_pem_private_key(priv_pem.encode("ascii"), password=None)
    signature = priv.sign(payload_bytes)
    return base64.b64encode(signature).decode("ascii"), key.get("public_key_pem"), key.get("id")


def _canonicalize(obj: dict) -> bytes:
    """RFC 8785 JCS-style canonical JSON (using sort_keys + separators).

    For full JCS conformance, a JCS library should be used. Python
    stdlib's json with sort_keys and compact separators is close
    enough for our local-dev purposes and is documented as such.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _make_claim(agent_id: str, claim_type: str, body: str, parent: str | None,
                ts: int, key_id: str, signature: str, public_key_pem: str) -> dict:
    claim_id = f"clm_{uuid.uuid4().hex[:16]}"
    claim = {
        "id": claim_id,
        "schema": "rollout-shield.claim/v1",
        "type": claim_type,
        "agent_id": agent_id,
        "ts": ts,
        "body": body,
        "parent": parent,
        "signing": {
            "key_id": key_id,
            "algorithm": "Ed25519",
            "public_key_pem": public_key_pem,
            "signature": signature,
            "canonicalization": "json-stable",
        },
    }
    return claim


def cmd_create(state: State, agent_id: str, claim_type: str, body: str,
               parent: str | None = None) -> dict:
    if claim_type not in VALID_TYPES:
        raise ValueError(f"invalid claim type: {claim_type}")
    ts = int(time.time())
    # find the signing key for this agent (so we can enforce the policy
    # before invoking the signer)
    from ..space import latest_key_for_agent, enforce_policy_for_key, PolicyViolation
    key = latest_key_for_agent(state, agent_id)
    if key is None:
        raise RuntimeError(
            f"no key registered for agent_id={agent_id}; "
            "run: rollout-shield keys new --agent-id " + agent_id
        )
    enforce_policy_for_key(state, action="claim_create", key_meta=key)
    # sign the body of the claim (everything except the signature)
    preimage = {
        "schema": "rollout-shield.claim/v1",
        "type": claim_type,
        "agent_id": agent_id,
        "ts": ts,
        "body": body,
        "parent": parent,
    }
    payload = _canonicalize(preimage)
    signature, pub_pem, key_id = _sign_with_key(state, agent_id, payload)
    claim = _make_claim(agent_id, claim_type, body, parent, ts,
                        key_id, signature, pub_pem)
    state.append_claim(claim)
    return claim


def cmd_claim(state: State, args: argparse.Namespace) -> int:
    sub = args.claim_command or "list"
    if sub == "list":
        return _claim_list(state, args)
    if sub == "create":
        try:
            claim = cmd_create(state, agent_id=args.agent_id, claim_type=args.type,
                               body=args.body, parent=args.parent)
        except Exception as exc:
            print(f"claim create failed: {exc}", file=__import__("sys").stderr)
            return 1
        if args.json:
            print(json.dumps(claim, indent=2, sort_keys=True))
        else:
            print(f"claim created: {claim['id']}")
            print(f"  type:     {claim['type']}")
            print(f"  agent:    {claim['agent_id']}")
            print(f"  parent:   {claim.get('parent')}")
            print(f"  ts:       {claim['ts']}")
            print(f"  signature:{claim['signing']['signature'][:24]}...")
        return 0
    if sub == "show":
        return _claim_show(state, args)
    print(f"unknown claim subcommand: {sub}", file=__import__("sys").stderr)
    return 2


def _claim_list(state: State, args: argparse.Namespace) -> int:
    claims = list(state.iter_claims(agent_id=args.agent_id, since_ts=args.since,
                                    limit=args.limit))
    # newest first
    claims.sort(key=lambda c: c.get("ts", 0), reverse=True)
    if args.json:
        print(json.dumps({"claims": claims}, indent=2))
        return 0
    if not claims:
        print("no claims recorded yet")
        return 0
    print(f"{len(claims)} claim(s):")
    for c in claims:
        print(f"  - {c['id']}  type={c['type']:<10}  agent={c['agent_id']}  "
              f"ts={c['ts']}  parent={c.get('parent')}")
    return 0


def _claim_show(state: State, args: argparse.Namespace) -> int:
    target_id = args.claim_id
    for c in state.iter_claims(limit=100000):
        if c.get("id") == target_id:
            if args.json:
                print(json.dumps(c, indent=2, sort_keys=True))
            else:
                for k, v in c.items():
                    if k == "signing" and isinstance(v, dict):
                        print(f"  signing:")
                        for sk, sv in v.items():
                            if isinstance(sv, str) and len(sv) > 80:
                                sv = sv[:80] + "..."
                            print(f"    {sk}: {sv}")
                    elif isinstance(v, str) and len(v) > 200:
                        print(f"  {k}: {v[:200]}...")
                    else:
                        print(f"  {k}: {v}")
            return 0
    print(f"no such claim: {target_id}", file=__import__("sys").stderr)
    return 1
