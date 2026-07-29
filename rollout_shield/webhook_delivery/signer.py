"""Outbound webhook signing.

Two signing modes are supported:

- ``hmac``  — HMAC-SHA256 over the canonical payload using a shared
  secret. The receiver verifies by recomputing the HMAC. Headers::

      X-Rollout-Shield-Signature: sha256=<hex>
      X-Rollout-Shield-Timestamp: <unix-seconds>
      X-Rollout-Shield-Key-Id: <signing_key>

- ``ed25519`` — Ed25519 signature over the canonical payload using the
  private key material looked up via ``rollout_shield.commands.keys``
  (the existing key subsystem). Headers::

      X-Rollout-Shield-Signature: ed25519=<base64>
      X-Rollout-Shield-Timestamp: <unix-seconds>
      X-Rollout-Shield-Key-Id: <signing_key>

Receivers verify by looking up the public key by ``key_id`` and
checking the signature against ``canonical_payload_bytes``.

Signing is intentionally optional (``sign_mode == "none"``); the
subsystem will not refuse to deliver without a signature but every
production-grade target SHOULD use one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from typing import Any

from .models import DeliveryTarget, SignMode, canonical_payload_bytes


def _require_cryptography() -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore[import-not-found]
            Ed25519PrivateKey,
        )
        return Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover — exercised in real installs
        raise RuntimeError(
            "ed25519 signing requires the 'cryptography' package; "
            "install with `pip install rollout-shield[crypto]`"
        ) from exc


def _load_ed25519_private(key_id: str, state: Any = None) -> Any:
    """Load an Ed25519 private key from the keys subsystem.

    Looks up the key metadata via ``State.get_key`` and reads the
    PEM file at ``private_key_path``. The actual private material is
    never exposed by this function — it returns a
    ``cryptography`` private-key object whose ``.sign()`` produces the
    signature.
    """
    from ..commands.keys import KEYS_MATERIAL_DIRNAME
    from ..state import DEFAULT_STATE_ROOT, State

    _require_cryptography()
    if state is None:
        state = State(root=DEFAULT_STATE_ROOT)
    meta = state.get_key(key_id)
    if meta is None:
        raise RuntimeError(f"unknown key_id: {key_id!r}")
    priv_path = Path(meta["private_key_path"])
    if not priv_path.exists():
        # fall back to <state>/keys_material/<key_id>.pem
        priv_path = state.root / KEYS_MATERIAL_DIRNAME / f"{key_id}.pem"
    if not priv_path.exists():
        raise RuntimeError(f"private key material not found for key_id={key_id!r}")
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(priv_path.read_bytes(), password=None)


def sign_payload_hmac(secret: str, payload_bytes: bytes) -> str:
    """Compute the HMAC-SHA256 hex digest for the canonical payload."""
    mac = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def sign_payload_ed25519(private_key: Any, payload_bytes: bytes) -> str:
    """Compute an Ed25519 signature, base64-encoded."""
    sig = private_key.sign(payload_bytes)
    return "ed25519=" + base64.b64encode(sig).decode("ascii")


def build_headers(target: DeliveryTarget, payload: dict[str, Any],
                  now: int | None = None,
                  state: Any = None) -> dict[str, str]:
    """Compute the outbound HTTP headers for a delivery.

    Always includes Content-Type and X-Rollout-Shield-* trace headers.
    When ``target.sign_mode`` is ``hmac`` or ``ed25519``, also includes
    the signature header.

    ``state`` is optional and only consulted for Ed25519 signing
    (which needs to look up the private key in state). Callers that
    use HMAC-only signing can omit it.
    """
    ts = int(time.time() if now is None else now)
    payload_bytes = canonical_payload_bytes(payload)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "rollout-shield-webhooks/1.0",
        "X-Rollout-Shield-Timestamp": str(ts),
    }
    if target.sign_mode == SignMode.HMAC:
        if not target.signing_key:
            raise ValueError(
                f"target {target.name!r} has sign_mode=hmac but no signing_key"
            )
        headers["X-Rollout-Shield-Signature"] = sign_payload_hmac(
            target.signing_key, payload_bytes
        )
        headers["X-Rollout-Shield-Key-Id"] = target.signing_key[:32]
    elif target.sign_mode == SignMode.ED25519:
        if not target.signing_key:
            raise ValueError(
                f"target {target.name!r} has sign_mode=ed25519 but no signing_key"
            )
        priv = _load_ed25519_private(target.signing_key, state=state)
        headers["X-Rollout-Shield-Signature"] = sign_payload_ed25519(priv, payload_bytes)
        headers["X-Rollout-Shield-Key-Id"] = target.signing_key
    elif target.sign_mode == SignMode.NONE:
        # No signature — explicitly mark so receivers know.
        headers["X-Rollout-Shield-Signature"] = "none"
    return headers
