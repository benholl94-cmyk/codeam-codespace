"""Health checks for the rollout-shield monitor.

Each check is a callable that returns a ``HealthResult``. Checks are
designed to be:

- **Pure**: no side effects beyond optional reads of the State.
- **Fast**: each check completes in <1s; the daemon runs them on a
  fixed interval (default 60s).
- **Independent**: a check may fail without affecting any other check.

Adding a new check means:

1. Implement a function that returns ``HealthResult``.
2. Register it in ``DEFAULT_CHECKS``.
3. (Optional) Add it to ``.rollout-shield/config.json`` under
   ``disabled_checks`` to disable per-deployment.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from .state import State


@dataclass
class HealthResult:
    name: str
    ok: bool
    message: str
    details: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# A check is ``Callable[[State], HealthResult]``.
Check = Callable[[State], HealthResult]


def _measure(fn: Callable[[], HealthResult]) -> HealthResult:
    start = time.perf_counter()
    try:
        return fn()
    finally:
        # If fn returned a result, ensure duration_ms is set.
        pass


# ---------- built-in checks ----------


def check_state_root_writable(state: State) -> HealthResult:
    """Ensure the state root is on a writable filesystem."""
    start = time.perf_counter()
    test_path = state.root / ".write-test"
    try:
        test_path.write_text("ok")
        test_path.unlink()
        return HealthResult(
            name="state_root_writable",
            ok=True,
            message=f"state root {state.root} is writable",
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except OSError as exc:
        return HealthResult(
            name="state_root_writable",
            ok=False,
            message=f"state root {state.root} not writable: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )


def check_disk_space(state: State, min_free_mb: int = 100) -> HealthResult:
    """Ensure at least ``min_free_mb`` free disk space on the state volume."""
    start = time.perf_counter()
    try:
        usage = shutil.disk_usage(state.root)
        free_mb = usage.free / (1024 * 1024)
        ok = free_mb >= min_free_mb
        return HealthResult(
            name="disk_space",
            ok=ok,
            message=f"{free_mb:.1f} MB free (min {min_free_mb} MB)",
            details={"free_mb": free_mb, "total_mb": usage.total / (1024 * 1024)},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except OSError as exc:
        return HealthResult(
            name="disk_space",
            ok=False,
            message=f"disk usage query failed: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )


def check_recent_claims(state: State, max_age_seconds: int = 86400) -> HealthResult:
    """Warn if no claims have been emitted in the last ``max_age_seconds``."""
    start = time.perf_counter()
    recent = state.recent_claims(n=1)
    if not recent:
        return HealthResult(
            name="recent_claims",
            ok=True,
            message="no claims yet (initial state)",
            details={"last_claim_ts": None},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    last = recent[0]
    age = time.time() - last.get("ts", 0)
    ok = age <= max_age_seconds
    return HealthResult(
        name="recent_claims",
        ok=ok,
        message=f"last claim was {int(age)}s ago",
        details={"last_claim_ts": last.get("ts"), "age_seconds": age, "threshold": max_age_seconds},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_alert_rate(state: State, max_alerts_per_hour: int = 10) -> HealthResult:
    """Warn if too many alerts were raised in the last hour."""
    start = time.perf_counter()
    since = int(time.time()) - 3600
    alerts = list(state.iter_alerts(since_ts=since, limit=1000))
    n = len(alerts)
    ok = n <= max_alerts_per_hour
    return HealthResult(
        name="alert_rate",
        ok=ok,
        message=f"{n} alerts in the last hour (max {max_alerts_per_hour})",
        details={"alerts_last_hour": n, "threshold": max_alerts_per_hour},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_controller_policy(state: State) -> HealthResult:
    """The current state is consistent with the configured controller policy.

    Fails if any non-quarantined key in the registry is not allowed
    under the active policy (e.g. human keys under device-only).
    Warns if any historical claim was signed by a now-rejected signer.
    """
    start = time.perf_counter()
    try:
        from .space import load_policy, validate_space
        policy = load_policy(state)
        consistent, violations = validate_space(state)
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="controller_policy",
            ok=False,
            message=f"could not evaluate controller policy: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    errors = [v for v in violations if v[0] == "error"]
    if not consistent:
        return HealthResult(
            name="controller_policy",
            ok=False,
            message=f"policy={policy}: {len(errors)} violations",
            details={"policy": policy, "violations": violations},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    return HealthResult(
        name="controller_policy",
        ok=True,
        message=f"policy={policy}: state consistent ({len(violations)} advisory)",
        details={"policy": policy, "violations": violations},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_keys_present(state: State) -> HealthResult:
    """Warn if no agent keys have been generated yet."""
    start = time.perf_counter()
    keys = state.list_keys()
    ok = len(keys) > 0
    return HealthResult(
        name="keys_present",
        ok=ok,
        message=f"{len(keys)} key(s) registered" if ok else "no agent keys registered; run `rollout-shield keys new`",
        details={"key_ids": [k.get("id") for k in keys]},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def check_self_reachable(state: State, host: str = "127.0.0.1",
                         port: int = 0, timeout: float = 0.5) -> HealthResult:
    """Smoke-test that the loopback interface is reachable.

    A simple, fast connectivity probe. The default port=0 means we
    only check the loopback resolution + socket creation, not a
    specific service.
    """
    start = time.perf_counter()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            # port=0 → don't actually connect; just exercise the API
            if port > 0:
                sock.connect((host, port))
            return HealthResult(
                name="loopback_reachable",
                ok=True,
                message=f"loopback {host} ok",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
    except OSError as exc:
        return HealthResult(
            name="loopback_reachable",
            ok=False,
            message=f"loopback {host} unreachable: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )


# ---------- registry ----------


DEFAULT_CHECKS: list[Check] = [
    check_state_root_writable,
    check_disk_space,
    check_recent_claims,
    check_alert_rate,
    check_controller_policy,
    check_keys_present,
    check_self_reachable,
]


def run_all_checks(state: State,
                   disabled: list[str] | None = None) -> list[dict]:
    """Run all enabled checks and return their results as dicts."""
    disabled = set(disabled or [])
    out: list[dict] = []
    for check in DEFAULT_CHECKS:
        name = check.__name__.replace("check_", "")
        if name in disabled:
            continue
        try:
            result = check(state)
            out.append(result.to_dict())
        except Exception as exc:  # noqa: BLE001 — health checks must never raise
            out.append({
                "name": name,
                "ok": False,
                "message": f"check raised: {exc}",
                "duration_ms": 0.0,
                "details": {"exception": repr(exc)},
            })
    return out


def aggregate(results: list[dict]) -> dict:
    """Aggregate a list of check results into a top-level health summary."""
    n = len(results)
    ok = sum(1 for r in results if r.get("ok"))
    degraded = sum(1 for r in results if not r.get("ok"))
    status = "healthy" if degraded == 0 else ("degraded" if ok > 0 else "unhealthy")
    return {
        "status": status,
        "total": n,
        "ok": ok,
        "degraded": degraded,
        "checks": results,
        "ts": int(time.time()),
    }
