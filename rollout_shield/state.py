"""Persistent state container for rollout-shield.

State lives on disk under a configurable root directory. All operations
are JSON-on-disk with atomic writes (write-temp + rename) so a crash
mid-write cannot corrupt the state.

Layout under the state root::

    <root>/
    ├── config.json              # runtime config (intervals, thresholds)
    ├── reputation.json          # agent → reputation index
    ├── claims/<agent_id>/<YYYY-MM>.jsonl   # append-only claim logs
    ├── alerts/<YYYY-MM-DD>.jsonl           # append-only alert log
    ├── keys/<key_id>.json       # agent key metadata (private key stays in TPM/HSM)
    └── health/<YYYY-MM-DD>.jsonl           # append-only health-check log

The `State` class is the only object that touches these files. The CLI,
the monitor daemon, and the HTTP API all share a single State instance
(read-only views for HTTP, write views for the daemon and CLI).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
DEFAULT_STATE_ROOT = Path(os.environ.get("ROLLOUT_SHIELD_STATE", ".rollout-shield"))


@dataclass
class StateConfig:
    state_root: Path = DEFAULT_STATE_ROOT
    monitor_interval_seconds: int = 60
    alert_webhook_url: str = ""
    claim_retention_days: int = 2555  # ~7 years
    health_window_seconds: int = 300
    reputation_decay_days: int = 30


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: temp file + rename.

    Guarantees the file at `path` is either the old content or the new
    content, never a partial write. POSIX rename is atomic on the same
    filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: Path, record: dict) -> None:
    """Append a single JSON record to a JSONL file (line-by-line)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


class State:
    """Persistent state container for rollout-shield."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else DEFAULT_STATE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root / "config.json"
        self.reputation_path = self.root / "reputation.json"
        self.claims_dir = self.root / "claims"
        self.alerts_dir = self.root / "alerts"
        self.health_dir = self.root / "health"
        self.keys_dir = self.root / "keys"
        for d in (self.claims_dir, self.alerts_dir, self.health_dir, self.keys_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._ensure_config()

    # ---------- config ----------

    def _ensure_config(self) -> None:
        if not self.config_path.exists():
            default = {
                "schema_version": SCHEMA_VERSION,
                "created_at": int(time.time()),
                "monitor_interval_seconds": 60,
                "alert_webhook_url": "",
                "claim_retention_days": 2555,
                "health_window_seconds": 300,
                "reputation_decay_days": 30,
            }
            atomic_write_json(self.config_path, default)

    def load_config(self) -> dict:
        with open(self.config_path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_config(self, cfg: dict) -> None:
        cfg["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.config_path, cfg)

    # ---------- reputation ----------

    def load_reputation(self) -> dict:
        if not self.reputation_path.exists():
            return {"schema_version": SCHEMA_VERSION, "agents": {}}
        with open(self.reputation_path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_reputation(self, rep: dict) -> None:
        rep["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.reputation_path, rep)

    def update_reputation(self, agent_id: str, delta: float, reason: str) -> None:
        rep = self.load_reputation()
        agents = rep.setdefault("agents", {})
        entry = agents.setdefault(agent_id, {"score": 0.0, "history": []})
        entry["score"] = round(entry.get("score", 0.0) + delta, 4)
        entry.setdefault("history", []).append({
            "ts": int(time.time()),
            "delta": delta,
            "reason": reason,
        })
        # keep history bounded to last 1000 events
        entry["history"] = entry["history"][-1000:]
        self.save_reputation(rep)

    # ---------- claims ----------

    def claim_path(self, agent_id: str, ts: int | None = None) -> Path:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return self.claims_dir / agent_id / f"{dt.strftime('%Y-%m')}.jsonl"

    def append_claim(self, claim: dict) -> Path:
        agent_id = claim.get("agent_id", "unknown")
        claim_id = claim.get("id") or f"clm_{uuid.uuid4().hex[:16]}"
        claim["id"] = claim_id
        claim.setdefault("ts", int(time.time()))
        path = self.claim_path(agent_id, claim["ts"])
        atomic_append_jsonl(path, claim)
        return path

    def iter_claims(self, agent_id: str | None = None,
                    since_ts: int | None = None,
                    limit: int | None = None) -> Iterator[dict]:
        if agent_id:
            agent_dirs = [self.claims_dir / agent_id]
        else:
            agent_dirs = sorted(p for p in self.claims_dir.iterdir() if p.is_dir())
        yielded = 0
        for agent_dir in agent_dirs:
            if not agent_dir.exists():
                continue
            for path in sorted(agent_dir.glob("*.jsonl")):
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        record = json.loads(line)
                        if since_ts is not None and record.get("ts", 0) < since_ts:
                            continue
                        yield record
                        yielded += 1
                        if limit is not None and yielded >= limit:
                            return

    def recent_claims(self, n: int = 50) -> list[dict]:
        return list(self.iter_claims(limit=n))

    # ---------- alerts ----------

    def alert_path(self, day_ts: int | None = None) -> Path:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(day_ts or time.time(), tz=timezone.utc)
        return self.alerts_dir / f"{dt.strftime('%Y-%m-%d')}.jsonl"

    def append_alert(self, alert: dict) -> Path:
        alert.setdefault("ts", int(time.time()))
        alert.setdefault("id", f"alt_{uuid.uuid4().hex[:16]}")
        path = self.alert_path(alert["ts"])
        atomic_append_jsonl(path, alert)
        return path

    def iter_alerts(self, since_ts: int | None = None,
                    limit: int | None = None) -> Iterator[dict]:
        yielded = 0
        for path in sorted(self.alerts_dir.glob("*.jsonl"), reverse=True):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if since_ts is not None and record.get("ts", 0) < since_ts:
                        continue
                    yield record
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return

    def recent_alerts(self, n: int = 50) -> list[dict]:
        return list(self.iter_alerts(limit=n))

    # ---------- health ----------

    def health_path(self, day_ts: int | None = None) -> Path:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(day_ts or time.time(), tz=timezone.utc)
        return self.health_dir / f"{dt.strftime('%Y-%m-%d')}.jsonl"

    def append_health(self, record: dict) -> Path:
        record.setdefault("ts", int(time.time()))
        path = self.health_path(record["ts"])
        atomic_append_jsonl(path, record)
        return path

    def latest_health(self) -> dict | None:
        files = sorted(self.health_dir.glob("*.jsonl"), reverse=True)
        for path in files:
            with open(path, encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
            if lines:
                return json.loads(lines[-1])
        return None

    # ---------- keys ----------

    def list_keys(self) -> list[dict]:
        out = []
        for path in sorted(self.keys_dir.glob("*.json")):
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    def get_key(self, key_id: str) -> dict | None:
        path = self.keys_dir / f"{key_id}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def put_key(self, key_id: str, key_data: dict) -> Path:
        path = self.keys_dir / f"{key_id}.json"
        atomic_write_json(path, key_data)
        return path

    # ---------- summary ----------

    def summary(self) -> dict:
        from datetime import datetime, timezone
        rep = self.load_reputation()
        agents = rep.get("agents", {})
        n_claims = sum(1 for _ in self.iter_claims(limit=100000))
        n_alerts = sum(1 for _ in self.iter_alerts(limit=100000))
        latest = self.latest_health()
        return {
            "schema_version": SCHEMA_VERSION,
            "state_root": str(self.root),
            "generated_at": int(time.time()),
            "agents": {
                "total": len(agents),
                "ids": sorted(agents.keys()),
            },
            "claims_count": n_claims,
            "alerts_count": n_alerts,
            "latest_health": latest,
            "version": "0.1.0",
        }
