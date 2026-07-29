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
import urllib.request
from typing import Any

from .state import State


SEVERITY_LEVELS = {"info", "warning", "error", "critical"}


def dispatch_alert(state: State, alert: dict,
                   webhook_url: str = "",
                   stderr: Any = None) -> dict:
    """Dispatch an alert through all configured channels.

    The alert is always written to the persistent alert log. If a
    webhook URL is configured, the alert is POSTed there. The alert
    is also written to stderr (configurable; defaults to sys.stderr).
    """
    if stderr is None:
        stderr = sys.stderr

    severity = alert.get("severity", "warning").lower()
    if severity not in SEVERITY_LEVELS:
        severity = "warning"

    # 1. persistent state
    alert_path = state.append_alert(alert)

    # 2. stderr
    payload = json.dumps(alert, sort_keys=True, ensure_ascii=False)
    print(f"[alert:{severity}] {payload}", file=stderr)

    # 3. webhook (best-effort)
    webhook_result = {"sent": False, "url": webhook_url, "reason": ""}
    if webhook_url:
        try:
            req = urllib.request.Request(
                webhook_url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                webhook_result["sent"] = True
                webhook_result["status"] = resp.status
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            webhook_result["reason"] = repr(exc)
            # write a follow-up record so the operator can see the failure
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
