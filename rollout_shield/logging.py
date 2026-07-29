"""Structured logging for rollout-shield.

Stdlib-only JSON logging with:

- 5 levels (DEBUG / INFO / WARNING / ERROR / CRITICAL)
- per-call structured fields (`extra={"key": "value"}`)
- request tracing via `trace_id` (auto-attached when set)
- rotation via the stdlib ``logging.handlers`` family
- log levels controllable via the ``ROLLOUT_SHIELD_LOG_LEVEL`` env var
- log format controllable via ``ROLLOUT_SHIELD_LOG_FORMAT`` =
  ``json`` (default) or ``text``

The module is **safe to import from anywhere** — it lazily installs
a handler on the root logger and never duplicates handlers if
imported twice.

Typical usage::

    from rollout_shield.logging import get_logger
    log = get_logger(__name__)
    log.info("claim verified", extra={"claim_id": cid, "agent": "default"})

For the daemon::

    from rollout_shield.logging import configure
    configure(level="INFO", logfile="~/.rollout-shield/daemon.log")
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

# --- trace context ---
# ContextVars follow the request/async boundary correctly. Daemon
# workers can attach a trace_id for the duration of a cycle.
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str | None = None) -> str:
    """Set the current trace id (random if not given) and return it."""
    if trace_id is None:
        trace_id = "trace-" + uuid.uuid4().hex[:16]
    _trace_id.set(trace_id)
    return trace_id


def get_trace_id() -> str | None:
    """Return the active trace id (or None)."""
    return _trace_id.get()


def clear_trace_id() -> None:
    """Reset the trace context."""
    _trace_id.set(None)


# --- format helpers ---

_RESERVED_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
})


class JsonFormatter(logging.Formatter):
    """One JSON object per log line.

    Includes the standard LogRecord fields plus any ``extra={...}``
    passed to the logging call. Reserved keys are namespaced under
    ``record`` to avoid collisions with custom extras.
    """

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # standard structured fields
        for key in ("pathname", "filename", "module", "funcName",
                    "lineno", "thread", "threadName", "process"):
            v = getattr(record, key, None)
            if v is not None:
                out[key] = v

        # trace context
        tid = _trace_id.get()
        if tid is not None:
            out["trace_id"] = tid

        # user-provided extras (skip reserved keys)
        for key, value in record.__dict__.items():
            if key in _RESERVED_KEYS or key.startswith("_"):
                continue
            if key in out:
                continue
            # only include JSON-serializable values
            try:
                json.dumps(value)
                out[key] = value
            except (TypeError, ValueError):
                out[key] = repr(value)

        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)

        return json.dumps(out, separators=(",", ":"), sort_keys=True)


class TextFormatter(logging.Formatter):
    """Plain text formatter for humans (CLI / one-shot commands)."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)sZ %(levelname)-7s %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def formatTime(self, record, datefmt=None):  # type: ignore[override]
        return time.strftime(datefmt or "%Y-%m-%dT%H:%M:%S",
                             time.gmtime(record.created))


# --- configuration ---

_DEFAULT_FORMAT = os.environ.get("ROLLOUT_SHIELD_LOG_FORMAT", "json")
_DEFAULT_LEVEL = os.environ.get("ROLLOUT_SHIELD_LOG_LEVEL", "INFO")


_configured = False
_config_lock = threading.Lock()


def configure(
    level: str | int | None = None,
    logfile: str | None = None,
    fmt: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure the root ``rollout_shield`` logger.

    Idempotent — repeated calls only adjust level / handler, never
    duplicate. Safe to call from CLI entry points and the daemon.
    """
    global _configured
    with _config_lock:
        logger = logging.getLogger("rollout_shield")
        # reset between configures (tests want a clean state)
        for h in list(logger.handlers):
            logger.removeHandler(h)

        level = level or _DEFAULT_LEVEL
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(level)

        fmt = fmt or _DEFAULT_FORMAT
        formatter: logging.Formatter
        if fmt == "json":
            formatter = JsonFormatter()
        else:
            formatter = TextFormatter()

        if logfile:
            logfile = os.path.expanduser(logfile)
            os.makedirs(os.path.dirname(logfile), exist_ok=True)
            handler: logging.Handler = logging.handlers.RotatingFileHandler(
                logfile,
                maxBytes=max_bytes,
                backupCount=backup_count,
            )
        else:
            handler = logging.StreamHandler(stream=sys.stderr)

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # do not propagate to root — the rollout_shield namespace is the
        # authoritative one.
        logger.propagate = False
        _configured = True
        return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger, configuring on first call."""
    if not _configured:
        configure()
    if name is None:
        return logging.getLogger("rollout_shield")
    if not name.startswith("rollout_shield"):
        name = "rollout_shield." + name
    return logging.getLogger(name)


# Convenience functions for the very common case where someone wants
# to log without explicit `get_logger(...)`.
def debug(msg: str, **extra: Any) -> None:
    get_logger().debug(msg, extra=extra)

def info(msg: str, **extra: Any) -> None:
    get_logger().info(msg, extra=extra)

def warning(msg: str, **extra: Any) -> None:
    get_logger().warning(msg, extra=extra)

def error(msg: str, **extra: Any) -> None:
    get_logger().error(msg, extra=extra)

def critical(msg: str, **extra: Any) -> None:
    get_logger().critical(msg, extra=extra)


__all__ = [
    "configure", "get_logger",
    "debug", "info", "warning", "error", "critical",
    "set_trace_id", "get_trace_id", "clear_trace_id",
    "JsonFormatter", "TextFormatter",
]
