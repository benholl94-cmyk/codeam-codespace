"""Data models for the webhook delivery subsystem.

Dataclasses are deliberately small and JSON-serializable. The runtime
persists them as JSON files under ``<state_root>/webhooks/`` using
the existing ``state.atomic_write_json`` helper.

All timestamps are stored as Unix epoch seconds (``int``). Conversion
to ISO-8601 happens at the HTTP boundary, not in storage.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# --- defaults ---------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: float = 10.0
DEFAULT_MAX_ATTEMPTS: int = 6  # 5 retries on top of the initial attempt
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (1, 4, 16, 64, 256)
DEFAULT_DEDUPE_WINDOW_SECONDS: int = 300  # 5 min
DEFAULT_CIRCUIT_BREAKER_THRESHOLD: int = 10  # consecutive failures before pause
DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 300


# --- enums ------------------------------------------------------------------


class SignMode(str, Enum):
    """How an outbound webhook delivery is signed."""

    NONE = "none"
    HMAC = "hmac"
    ED25519 = "ed25519"


class DeliveryStatus(str, Enum):
    """Lifecycle of a single delivery."""

    PENDING = "pending"          # waiting in outbox
    IN_FLIGHT = "in_flight"      # dispatcher has sent the request
    DELIVERED = "delivered"      # 2xx response received
    FAILED = "failed"            # retryable failure (will be re-attempted)
    DLQ = "dlq"                  # gave up; moved to dead-letter
    CANCELLED = "cancelled"      # operator cancelled


# --- dataclasses ------------------------------------------------------------


@dataclass
class DeliveryTarget:
    """A configured outbound webhook destination."""

    name: str
    url: str
    sign_mode: SignMode = SignMode.NONE
    signing_key: str = ""           # HMAC secret OR Ed25519 key_id
    description: str = ""
    enabled: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    dedupe_window_seconds: int = DEFAULT_DEDUPE_WINDOW_SECONDS
    fail_streak: int = 0
    paused_until: int = 0           # Unix ts; 0 means not paused
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sign_mode"] = self.sign_mode.value
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeliveryTarget:
        data = dict(raw)
        data["sign_mode"] = SignMode(data.get("sign_mode", "none"))
        return cls(**data)

    def is_paused(self, now: int | None = None) -> bool:
        if self.paused_until == 0:
            return False
        return int(time.time() if now is None else now) < self.paused_until


@dataclass
class AttemptRecord:
    """A single delivery attempt."""

    attempt_no: int
    started_at: int
    finished_at: int = 0
    http_status: int = 0
    error: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AttemptRecord:
        return cls(**raw)


@dataclass
class DeliveryRecord:
    """A single outbound delivery, persisted to the outbox."""

    delivery_id: str
    target_name: str
    payload: dict[str, Any]
    payload_sha256: str
    idempotency_key: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: list[AttemptRecord] = field(default_factory=list)
    attempt_count: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    next_attempt_at: int = field(default_factory=lambda: int(time.time()))
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    last_error: str = ""
    trace_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["attempts"] = [a.to_dict() for a in self.attempts]
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeliveryRecord:
        data = dict(raw)
        data["status"] = DeliveryStatus(data.get("status", "pending"))
        data["attempts"] = [AttemptRecord.from_dict(a) for a in data.get("attempts", [])]
        return cls(**data)


# --- helpers ----------------------------------------------------------------


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Canonicalize a payload for signing.

    Uses RFC-8785-style deterministic JSON: sorted keys, no
    insignificant whitespace, UTF-8. This matches the pattern used
    elsewhere in rollout-shield (claim canonicalization).
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical payload."""
    return "sha256:" + hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def new_delivery_id() -> str:
    """Return a new delivery identifier (ULID-shaped; uuid4 hex is fine here)."""
    return "del-" + uuid.uuid4().hex[:24]


def new_trace_id() -> str:
    """Return a new short trace identifier."""
    return "trace-" + uuid.uuid4().hex[:16]
