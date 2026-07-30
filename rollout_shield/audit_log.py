"""Append-only access audit log.

Every state-mutating call site can emit one of these records. The log
lives at ``<state_root>/access/<YYYY-MM-DD>.jsonl`` and is rotated by
day. Records never contain private key material or webhook payloads.

A record looks like::

    {"ts": 1753..., "action": "claim.create", "actor": "agent:alice",
     "target": "clm_abc", "result": "ok", "detail": {}}
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _log_path(state_root: Path) -> Path:
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    p = Path(state_root) / "access" / f"{day}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_access_event(state_root: Path, *, action: str,
                         actor: str, target: str = "",
                         result: str = "ok",
                         detail: dict | None = None) -> None:
    """Append a single access event to today's audit log.

    Failures here never propagate — the audit log is best-effort. If
    the log directory is unwritable (e.g., state root is read-only),
    the access event is silently dropped; the actual operation should
    still succeed or fail on its own merits.
    """
    try:
        path = _log_path(state_root)
        record = {
            "ts": int(time.time()),
            "action": action,
            "actor": actor,
            "target": target,
            "result": result,
            "detail": detail or {},
        }
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass  # best-effort; never let audit-log failure break the call


def iter_access_events(state_root: Path,
                       since_ts: int | None = None,
                       limit: int | None = None):
    """Yield access records newest-first."""
    log_dir = Path(state_root) / "access"
    if not log_dir.exists():
        return
    yielded = 0
    for path in sorted(log_dir.glob("*.jsonl"), reverse=True):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if since_ts is not None and rec.get("ts", 0) < since_ts:
                    continue
                yield rec
                yielded += 1
                if limit is not None and yielded >= limit:
                    return