"""Alert dispatch for the rollout-shield monitor.

When a health check fails (or a high-severity event occurs), the
monitor records an alert in persistent state and dispatches it via
configured channels. Supported channels:

- **log**: write to stderr (always on)
- **webhook**: POST JSON to ``config.alert_webhook_url`` (Slack,
  PagerDuty, Discord, custom endpoints all accept JSON)
- **file**: append to the daily alert log in state (always on)

The alerter is intentionally tiny. Routing, deduplication, and
acknowledgement are the receiver's responsibility.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .state import State


SEVERITY_LEVELS = {"info", "warning", "error", "critical"}

# G4: webhook URL allowlist. urllib.request.urlopen accepts any scheme
# including file:// (would write the alert JSON to a local file) and
# ftp://, plus loopback hosts (would let the monitor probe localhost
# services) and the cloud metadata IP 169.254.169.254. Restrict to
# http/https to non-loopback, non-metadata hosts.
ALLOWED_WEBHOOK_SCHEMES: tuple[str, ...] = ("http", "https")
REFUSED_WEBHOOK_HOSTS: frozenset[str] = frozenset({
    "127.0.0.1", "::1", "localhost", "0.0.0.0", "::",
    "169.254.169.254",  # AWS / GCP / Azure instance metadata
})

# Fields stripped from the alert before POSTing. These often contain
# internal-health details (exception reprs, stack frames, full check
# output) that we don't want to leak to an external webhook receiver.
WEBHOOK_REDACT_KEYS: frozenset[str] = frozenset({
    "summary", "details", "exception", "traceback",
    "exception_repr", "stack", "stdout", "stderr",
})


def dispatch_alert(state: State, alert: dict,
                   webhook_url: str = "",
                   stderr: Any = None) -> dict:
    """Dispatch an alert through all configured channels.

    The alert is always written to the persistent alert log. If a
    webhook URL is configured AND it passes the G4 scheme/host
    allowlist, the (redacted) alert is POSTed there. The alert is
    also written to stderr (configurable; defaults to sys.stderr).
    """
    if stderr is None:
        stderr = sys.stderr

    severity = alert.get("severity", "warning").lower()
    if severity not in SEVERITY_LEVELS:
        severity = "warning"

    # 1. persistent state
    alert_path = state.append_alert(alert)

    # 2. stderr (always; full alert, no redaction)
    full_payload = json.dumps(alert, sort_keys=True, ensure_ascii=False)
    print(f"[alert:{severity}] {full_payload}", file=stderr)

    # 3. webhook (best-effort, with G4 allowlist + payload redaction)
    webhook_result = {"sent": False, "url": webhook_url, "reason": ""}
    if webhook_url:
        try:
            parsed = urllib.parse.urlparse(webhook_url)
            if parsed.scheme not in ALLOWED_WEBHOOK_SCHEMES:
                webhook_result["reason"] = (
                    f"refused webhook scheme {parsed.scheme!r}; "
                    f"allowed: {list(ALLOWED_WEBHOOK_SCHEMES)}"
                )
            elif (parsed.hostname or "").lower() in REFUSED_WEBHOOK_HOSTS:
                webhook_result["reason"] = (
                    f"refused webhook host {parsed.hostname!r} "
                    f"(loopback or metadata IP)"
                )
            else:
                # G4: redact internal-detail fields before posting
                safe_alert = {
                    k: v for k, v in alert.items()
                    if k not in WEBHOOK_REDACT_KEYS
                }
                safe_payload = json.dumps(
                    safe_alert, sort_keys=True, ensure_ascii=False
                )
                req = urllib.request.Request(
                    webhook_url,
                    data=safe_payload.encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "rollout-shield/0.4.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    webhook_result["sent"] = True
                    webhook_result["status"] = resp.status
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as exc:
            webhook_result["reason"] = repr(exc)
            state.append_alert({
                "severity": "warning",
                "source": "alerter.webhook",
                "message": f"webhook delivery failed: {exc}",
                "original_alert_ts": alert.get("ts"),
            })
        except Exception as exc:  # noqa: BLE001 — never let alert dispatch kill the daemon
            webhook_result["reason"] = repr(exc)

    return {
        "alert_id": alert.get("id"),
        "alert_path": str(alert_path),
        "severity": severity,
        "webhook": webhook_result,
        "ts": alert.get("ts", int(time.time())),
    }
