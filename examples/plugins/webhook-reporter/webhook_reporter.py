"""webhook-reporter plugin (example).

Posts every `claim_verify` event to a webhook URL configured via the
environment variable ``WEBHOOK_REPORTER_URL``. No-op when the env var
is unset.

This plugin demonstrates the extension contract — it does NOT mutate
state and never crashes the host runtime.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any


_LAST_DELIVERY: dict[str, Any] = {"ok": None, "ts": None, "error": None}


def on_claim_verify(**kwargs: Any) -> dict:
    """Hook: claim_verify. Dispatched by the runtime after a verify."""
    return _post("claim_verify", kwargs)


def on_monitor_cycle(**kwargs: Any) -> dict:
    """Hook: monitor_cycle. Dispatched at the end of each cycle."""
    return _post("monitor_cycle", kwargs)


def _post(event: str, payload: dict) -> dict:
    url = os.environ.get("WEBHOOK_REPORTER_URL", "").strip()
    if not url:
        return {"dispatched": False, "reason": "no_url"}
    body = json.dumps({"event": event, "payload": payload}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            _LAST_DELIVERY.update({
                "ok": True, "ts": int(__import__("time").time()),
                "status": resp.status,
            })
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _LAST_DELIVERY.update({
            "ok": False, "ts": int(__import__("time").time()),
            "error": repr(exc),
        })
    return dict(_LAST_DELIVERY)


def status(**_kwargs: Any) -> dict:
    """Exposed as a plugin command (not a hook)."""
    return dict(_LAST_DELIVERY)