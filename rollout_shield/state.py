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

import contextlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX-only; Windows uses a different scheme
    import fcntl as _fcntl  # type: ignore[import-not-found]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


SCHEMA_VERSION = 1
DEFAULT_STATE_ROOT = Path(os.environ.get("ROLLOUT_SHIELD_STATE", ".rollout-shield"))


# Forbidden parents for any state root. Operating inside these would
# mean the rollout-shield state tree competes with system-managed
# directories (packages, daemons, log rotation, etc.) and is almost
# certainly a misconfiguration or an attack. Refuse early.
FORBIDDEN_STATE_PARENTS: tuple[Path, ...] = (
    Path("/etc"), Path("/var"), Path("/usr"),
    Path("/bin"), Path("/sbin"), Path("/boot"),
    Path("/proc"), Path("/sys"), Path("/dev"),
    Path("/lib"), Path("/lib64"), Path("/opt"),
)


def _validate_state_root(root: Path) -> None:
    """Reject privileged or foreign-owned state roots (G5).

    Raises ``ValueError`` if the resolved path lives inside a system
    directory, if the running uid is 0 without explicit opt-in, or if
    the directory already exists but is owned by another uid.
    Operators can override the root-uid check via
    ``ROLLOUT_SHIELD_ALLOW_ROOT_STATE=1`` and the other-owner check
    via ``ROLLOUT_SHIELD_ALLOW_OTHER_OWNER=1``.
    """
    resolved = root.resolve()
    for fp in FORBIDDEN_STATE_PARENTS:
        if resolved.is_relative_to(fp):
            raise ValueError(
                f"refusing state root {resolved}: lives inside "
                f"system directory {fp}; choose a path under "
                f"$HOME, /srv, /workspace, or another user-owned location"
            )
    if os.geteuid() == 0 and not os.environ.get("ROLLOUT_SHIELD_ALLOW_ROOT_STATE"):
        raise ValueError(
            "refusing to operate on a state root as uid=0; "
            "set ROLLOUT_SHIELD_ALLOW_ROOT_STATE=1 to override"
        )
    if resolved.exists() and not os.environ.get("ROLLOUT_SHIELD_ALLOW_OTHER_OWNER"):
        try:
            st = resolved.stat()
        except OSError:
            return
        if st.st_uid != os.geteuid():
            raise ValueError(
                f"refusing state root {resolved}: owned by uid "
                f"{st.st_uid}, but we are uid {os.geteuid()}; "
                f"set ROLLOUT_SHIELD_ALLOW_OTHER_OWNER=1 to override"
            )


# Migration registry: MIGRATIONS[(from_version, to_version)] -> callable(dict) -> dict.
# Each migration is a pure function that maps an old-version state dict to a
# new-version state dict, preserving data semantics and never destructive.
#
# When bumping SCHEMA_VERSION, add a new migration here:
#
#     @register_migration(from_version=1, to_version=2)
#     def _migrate_v1_to_v2(state: dict) -> dict:
#         state.setdefault("new_field", default_value)
#         state["schema_version"] = 2
#         return state
#
# Migrations must be ordered: bump from SCHEMA_VERSION-1, then -2, etc.

_MIGRATIONS: dict[tuple[int, int], "callable"] = {}


def register_migration(from_version: int, to_version: int):
    """Decorator: register a state-migration function for (from, to) version."""
    def deco(fn):
        _MIGRATIONS[(from_version, to_version)] = fn
        return fn
    return deco


def migrate(state: dict, *, target: int = SCHEMA_VERSION) -> dict:
    """Run any registered migrations until ``state`` reaches ``target`` version.

    If a state has no ``schema_version`` (legacy data), treat it as v0 and
    attempt to migrate forward. Unknown (legacy-without-migration) states are
    left alone but a warning is emitted via the ``__migration_warnings__``
    key on the returned state — callers can surface this without raising.
    """
    warnings = list(state.get("__migration_warnings__", []))
    current = int(state.get("schema_version", 0))
    if current > target:
        warnings.append(
            f"state at schema_version={current} is newer than target={target}; "
            f"running an older binary against newer state may be unsafe"
        )
    while current < target:
        step = _MIGRATIONS.get((current, current + 1))
        if step is None:
            warnings.append(
                f"no migration registered for v{current} -> v{current + 1}; "
                f"state left at v{current}"
            )
            break
        state = step(state)
        current = int(state.get("schema_version", current))
    state["__migration_warnings__"] = warnings
    return state


class StateLockError(RuntimeError):
    """Raised when the state root cannot be locked for write.

    Caught by callers and surfaced as an actionable message rather than a raw
    ``BlockingIOError`` or ``PermissionError`` traceback.
    """


def lock_path(state_root: Path) -> Path:
    """Return the path of the state write-lock file (``<root>/.write.lock``).

    The lock is advisory: it prevents two writer processes (monitor + CLI)
    from interleaving atomic_write_json calls, but does not block readers.
    """
    return Path(state_root) / ".write.lock"


@contextlib.contextmanager
def write_lock(state_root: Path, blocking: bool = True):
    """Acquire the per-state write lock. Use as a context manager:

        with write_lock(state.root):
            state.save_config({...})

    Locks are auto-released on exit. On non-POSIX systems, the lock is a no-op
    (single-process tests still pass; multi-process safety reduces to
    "trust the OS"). The lock file is created lazily and reused.
    """
    lockfile = lock_path(state_root)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    fh = None
    try:
        if _HAS_FCNTL:
            fh = open(lockfile, "w", encoding="utf-8")
            mode = _fcntl.LOCK_EX if blocking else _fcntl.LOCK_EX | _fcntl.LOCK_NB
            try:
                _fcntl.flock(fh.fileno(), mode)
            except BlockingIOError as exc:
                # Detach fh so the finally-block's flock+close don't operate
                # on the already-closed handle.
                closed_fh = fh
                fh = None
                closed_fh.close()
                raise StateLockError(
                    f"another writer holds the state lock at {lockfile}; "
                    f"set blocking=False to fail fast, or wait and retry"
                ) from exc
        else:  # pragma: no cover - non-POSIX branch
            fh = open(lockfile, "w", encoding="utf-8")
        yield fh
    finally:
        if fh is not None:
            if _HAS_FCNTL:
                try:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                except OSError:
                    pass
            fh.close()


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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise StateLockError(
            f"cannot create state directory {path.parent}: {exc}; "
            f"check filesystem permissions or set ROLLOUT_SHIELD_STATE to a "
            f"writable location"
        ) from exc

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        # fsync the directory so the rename itself is durable on POSIX
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass  # not all filesystems support dir fsync
        finally:
            os.close(dir_fd)
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
        _validate_state_root(self.root)        # G5: refuse privileged paths
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
                "installed_at": int(time.time()),
                "installed_by": "rollout-shield install",
            }
            atomic_write_json(self.config_path, default)

    def load_config(self) -> dict:
        with open(self.config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return migrate(cfg)

    def save_config(self, cfg: dict) -> None:
        cfg["schema_version"] = SCHEMA_VERSION
        # drop transient migration warnings before persisting
        cfg.pop("__migration_warnings__", None)
        atomic_write_json(self.config_path, cfg)

    # ---------- reputation ----------

    def load_reputation(self) -> dict:
        if not self.reputation_path.exists():
            return {"schema_version": SCHEMA_VERSION, "agents": {}}
        with open(self.reputation_path, encoding="utf-8") as fh:
            rep = json.load(fh)
        migrated = migrate(rep)
        if migrated is not rep:
            # persist the migrated copy so subsequent loads are fast
            migrated.pop("__migration_warnings__", None)
            migrated["schema_version"] = SCHEMA_VERSION
            atomic_write_json(self.reputation_path, migrated)
        return migrated

    def save_reputation(self, rep: dict) -> None:
        rep["schema_version"] = SCHEMA_VERSION
        rep.pop("__migration_warnings__", None)
        atomic_write_json(self.reputation_path, rep)

    def update_reputation(self, agent_id: str, delta: float, reason: str,
                          *, actor: str | None = None) -> None:
        # G3: require an authenticated principal, a sane delta, and
        # self-attribution (actor == agent_id) until delegation tokens
        # are implemented. The CLI passes actor=agent_id for claim
        # emissions and actor="cli:verify" (or the calling uid) for
        # verifications.
        from .commands.keys import validate_agent_id
        validate_agent_id(agent_id)
        if actor is None:
            raise RuntimeError(
                "update_reputation requires an authenticated actor; "
                "callers must pass actor=... explicitly"
            )
        if abs(delta) > 1.0:
            raise RuntimeError(
                f"reputation delta {delta} exceeds +/-1.0 cap"
            )
        if actor != agent_id and not actor.startswith("cli:"):
            raise RuntimeError(
                f"actor {actor!r} is not authorized to update "
                f"reputation for {agent_id!r}; only self-update is "
                f"permitted (delegation tokens not yet implemented)"
            )
        rep = self.load_reputation()
        agents = rep.setdefault("agents", {})
        entry = agents.setdefault(agent_id, {"score": 0.0, "history": []})
        # cap the absolute score at +/-100.0
        new_score = round(entry.get("score", 0.0) + delta, 4)
        entry["score"] = max(-100.0, min(100.0, new_score))
        entry.setdefault("history", []).append({
            "ts": int(time.time()),
            "delta": delta,
            "reason": reason,
            "actor": actor,
        })
        # keep history bounded to last 1000 events
        entry["history"] = entry["history"][-1000:]
        self.save_reputation(rep)

    # ---------- claims ----------

    def claim_path(self, agent_id: str, ts: int | None = None) -> Path:
        from datetime import datetime, timezone
        # Defense in depth: even though the CLI validates, reject any
        # agent_id that could escape the state root if it ever reaches
        # this layer (e.g. via direct API call).
        from .commands.keys import validate_agent_id
        validate_agent_id(agent_id)
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
            from .commands.keys import validate_agent_id
            validate_agent_id(agent_id)
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
