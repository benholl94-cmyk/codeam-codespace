"""Verify a claim's signature."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from ..state import State


def cmd_verify(state: State, args: argparse.Namespace) -> int:
    target_id = args.claim_id
    for claim in state.iter_claims(limit=100000):
        if claim.get("id") != target_id:
            continue
        sig_block = claim.get("signing", {})
        pub_pem = sig_block.get("public_key_pem", "")
        sig_b64 = sig_block.get("signature", "")
        if not pub_pem or not sig_b64:
            result = {"ok": False, "reason": "missing signature or public key"}
            break
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            from cryptography.exceptions import InvalidSignature
        except ImportError as exc:
            print(f"verify failed: cryptography package missing: {exc}",
                  file=__import__("sys").stderr)
            return 1

        try:
            pub = load_pem_public_key(pub_pem.encode("ascii"))
        except Exception as exc:
            result = {"ok": False, "reason": f"public key load failed: {exc}"}
            break

        # Recreate the preimage that was signed
        preimage = {
            "schema": claim.get("schema"),
            "type": claim.get("type"),
            "agent_id": claim.get("agent_id"),
            "ts": claim.get("ts"),
            "body": claim.get("body"),
            "parent": claim.get("parent"),
        }
        payload = json.dumps(preimage, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        sig_bytes = base64.b64decode(sig_b64.encode("ascii"))
        try:
            pub.verify(sig_bytes, payload)
            result = {"ok": True, "claim_id": target_id,
                      "agent_id": claim.get("agent_id"),
                      "key_id": sig_block.get("key_id")}
            # reward honest signing — small, capped per claim
            try:
                state.update_reputation(result["agent_id"], 0.01, "verify:ok")
            except Exception:
                pass  # never let reputation write break verify
        except InvalidSignature:
            result = {"ok": False, "reason": "signature does not verify",
                      "claim_id": target_id, "agent_id": claim.get("agent_id")}
            try:
                state.update_reputation(claim.get("agent_id") or "unknown",
                                        -0.05, "verify:bad-signature")
            except Exception:
                pass
        except Exception as exc:
            result = {"ok": False, "reason": f"verify error: {exc}",
                      "claim_id": target_id, "agent_id": claim.get("agent_id")}
            try:
                state.update_reputation(claim.get("agent_id") or "unknown",
                                        -0.01, "verify:error")
            except Exception:
                pass
        break
    else:
        result = {"ok": False, "reason": f"no such claim: {target_id}"}

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result["ok"]:
            print(f"OK  {result['claim_id']}  signed by {result['agent_id']} (key {result['key_id']})")
            return 0
        print(f"FAIL  {result.get('reason')}")
        return 1
