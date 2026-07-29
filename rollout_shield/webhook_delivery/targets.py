"""Webhook target registry.

A target is a named, persistent configuration for an outbound
webhook destination. Targets are stored as one JSON file per name
under ``<state_root>/webhooks/targets/`` using
``state.atomic_write_json``. The dispatcher looks up the target on
every delivery attempt.

The ``circuit-breaker`` is intentionally simple: a target whose
consecutive failure count exceeds the threshold (default 10) is
marked paused until ``paused_until`` passes. Operators can
``record_success`` to reset the streak.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..state import State, atomic_write_json
from .models import (
    DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    DeliveryTarget,
    SignMode,
)


class TargetError(Exception):
    """Raised when a target operation fails (validation, persistence, etc.)."""


def _targets_dir(state: State) -> Path:
    d = state.root / "webhooks" / "targets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _target_path(state: State, name: str) -> Path:
    if "/" in name or name in {"", ".", ".."}:
        raise TargetError(f"invalid target name: {name!r}")
    return _targets_dir(state) / f"{name}.json"


def add_target(state: State, name: str, url: str,
               sign_mode: str | SignMode = "none",
               signing_key: str = "",
               description: str = "",
               timeout_seconds: float = 10.0,
               max_attempts: int = 6,
               dedupe_window_seconds: int = 300,
               enabled: bool = True) -> DeliveryTarget:
    """Create or update a target. Persists atomically and returns the result."""
    if not name:
        raise TargetError("target name is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise TargetError(f"target URL must be http(s): got {url!r}")
    mode = SignMode(sign_mode) if not isinstance(sign_mode, SignMode) else sign_mode
    if mode in (SignMode.HMAC, SignMode.ED25519) and not signing_key:
        raise TargetError(f"sign_mode={mode.value} requires a non-empty signing_key")

    now = int(time.time())
    target = DeliveryTarget(
        name=name,
        url=url,
        sign_mode=mode,
        signing_key=signing_key,
        description=description,
        enabled=enabled,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        dedupe_window_seconds=dedupe_window_seconds,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(_target_path(state, name), target.to_dict())
    return target


def get_target(state: State, name: str) -> DeliveryTarget | None:
    """Load a target by name. Returns None if absent."""
    path = _target_path(state, name)
    if not path.exists():
        return None
    import json
    return DeliveryTarget.from_dict(json.loads(path.read_text()))


def list_targets(state: State) -> list[DeliveryTarget]:
    """Return all targets sorted by name."""
    out: list[DeliveryTarget] = []
    import json
    for path in sorted(_targets_dir(state).glob("*.json")):
        try:
            out.append(DeliveryTarget.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError, ValueError):
            # skip corrupt files; a healthy system should not have any
            continue
    return out


def remove_target(state: State, name: str) -> bool:
    """Delete a target. Returns True if removed, False if it didn't exist."""
    path = _target_path(state, name)
    if not path.exists():
        return False
    path.unlink()
    return True


def record_success(state: State, name: str) -> None:
    """Reset the circuit-breaker streak after a successful delivery."""
    tgt = get_target(state, name)
    if tgt is None:
        return
    if tgt.fail_streak == 0 and tgt.paused_until == 0:
        return  # nothing to do; avoid touching mtime
    tgt.fail_streak = 0
    tgt.paused_until = 0
    tgt.updated_at = int(time.time())
    atomic_write_json(_target_path(state, name), tgt.to_dict())


def record_failure(state: State, name: str,
                   threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
                   cooldown: int = DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS) -> bool:
    """Increment the circuit-breaker streak after a failed delivery.

    Returns True if the breaker tripped (target is now paused).
    """
    tgt = get_target(state, name)
    if tgt is None:
        return False
    tgt.fail_streak += 1
    if tgt.fail_streak >= threshold and tgt.paused_until == 0:
        tgt.paused_until = int(time.time()) + cooldown
        atomic_write_json(_target_path(state, name), tgt.to_dict())
        return True
    tgt.updated_at = int(time.time())
    atomic_write_json(_target_path(state, name), tgt.to_dict())
    return False
