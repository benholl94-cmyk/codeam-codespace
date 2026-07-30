#!/usr/bin/env python3
"""secure_state.py — Fernet-encrypted wrapper over rollout_shield.state.

Goal: zero data leak at rest. Sensitive state files (config.json,
reputation.json, claim signing blocks) are stored as Fernet tokens
that can only be decrypted when the owner has placed the unlock file
at ``.audit/.owner_unlock``.

Without the unlock file:
  * write attempts raise ``StateLockedError`` (refused)
  * read attempts return ``None`` (no data exposed)
  * no plaintext state files ever touch disk

Unlock file format:
  * 32 raw bytes OR a urlsafe-base64-encoded 32-byte Fernet key
  * mode 0600 (owner-read/write only)
  * gitignored — never committed

This is **defense in depth**, not a primary access control: a determined
attacker with root access can still read memory. But it stops:
  * accidental commits of state
  * cloud-synced backups syncing plaintext
  * third-party file scanners / cloud indexing
  * log aggregators that pick up JSONL files
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except Exception:  # pragma: no cover
    _HAS_FERNET = False


UNLOCK_PATH = Path(os.environ.get("ROLLOUT_SHIELD_UNLOCK", ".audit/.owner_unlock"))


class StateLockedError(RuntimeError):
    """Raised when an operation requires the owner unlock file and it's absent."""


def _read_unlock() -> bytes:
    """Read the Fernet key from the unlock file. Raise if missing/unreadable."""
    if not UNLOCK_PATH.exists():
        raise StateLockedError(
            f"owner unlock missing at {UNLOCK_PATH}; "
            f"refusing to operate on encrypted state. "
            f"Generate one with: python3 tools/secure_state.py --init"
        )
    try:
        data = UNLOCK_PATH.read_bytes()
        if len(data) == 44:
            # urlsafe-base64 32-byte key (Fernet format)
            return base64.urlsafe_b64decode(data)
        if len(data) == 32:
            return data
        # last resort: treat as utf-8 urlsafe base64
        return base64.urlsafe_b64decode(data.strip())
    except OSError as exc:
        raise StateLockedError(f"cannot read unlock at {UNLOCK_PATH}: {exc}")


def is_unlocked() -> bool:
    """Quick check: is the owner unlock present? Used by readers."""
    return UNLOCK_PATH.exists()


def _fernet() -> "Fernet":
    if not _HAS_FERNET:
        raise StateLockedError(
            "cryptography.fernet unavailable; install cryptography first"
        )
    key = _read_unlock()
    if len(key) != 32:
        raise StateLockedError(
            f"unlock key must be 32 raw bytes or urlsafe-base64; got {len(key)}"
        )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: Any) -> bytes:
    """Encrypt a JSON-serializable value. Returns a Fernet token."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(payload)


def decrypt_json(token: bytes) -> Any:
    """Decrypt a Fernet token back into the original value."""
    try:
        payload = _fernet().decrypt(token)
    except InvalidToken as exc:
        raise StateLockedError(
            f"unlock does not match encrypted state at this path; "
            f"if state was encrypted with a different key, it is unrecoverable"
        ) from exc
    return json.loads(payload)


def encrypted_atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to disk as a Fernet token (atomic: temp + rename)."""
    if not is_unlocked():
        raise StateLockedError(
            f"refusing to write encrypted state without owner unlock at {UNLOCK_PATH}"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise StateLockedError(f"cannot create {path.parent}: {exc}") from exc
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encrypt_json(data))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def encrypted_read_json(path: Path) -> Any | None:
    """Read encrypted JSON. Returns None if unlock missing (no info leak)."""
    if not is_unlocked():
        return None
    if not path.exists():
        return None
    return decrypt_json(path.read_bytes())


@contextlib.contextmanager
def unlocked_only():
    """Context manager: code inside only runs when the unlock is present.

    Use to gate sensitive operations::

        with unlocked_only():
            state.save_reputation({...})
    """
    if not is_unlocked():
        raise StateLockedError(
            f"operation requires owner unlock at {UNLOCK_PATH}"
        )
    yield


def cmd_init(args: argparse.Namespace) -> int:  # type: ignore[name-defined]
    """Generate a new 32-byte unlock key. DESTRUCTIVE — overwrites existing."""
    import argparse
    parser = argparse.ArgumentParser(prog="secure_state --init")
    if UNLOCK_PATH.exists() and not args.force:
        print(f"unlock already exists at {UNLOCK_PATH}; pass --force to overwrite",
              file=__import__("sys").stderr)
        return 1
    UNLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key() if _HAS_FERNET else os.urandom(32)
    UNLOCK_PATH.write_bytes(key)
    os.chmod(UNLOCK_PATH, 0o600)
    print(f"unlock written: {UNLOCK_PATH}")
    print(f"  bytes:  {len(key)}")
    print(f"  mode:   0600 (owner-rw only)")
    print()
    print("BACKUP THIS KEY OFFLINE. Loss of this key = loss of all encrypted state.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:  # type: ignore[name-defined]
    import argparse, sys
    parser = argparse.ArgumentParser(prog="secure_state --status")
    if UNLOCK_PATH.exists():
        print(f"unlock:    present at {UNLOCK_PATH}")
        try:
            st = UNLOCK_PATH.stat()
            print(f"  size:    {st.st_size} bytes")
            print(f"  mode:    {oct(st.st_mode & 0o777)}")
            print(f"  uid/gid: {st.st_uid}:{st.st_gid}")
            if (st.st_mode & 0o077) != 0:
                print("  WARNING: unlock is group/world readable — fix with chmod 600")
        except OSError as exc:
            print(f"  ERROR: {exc}")
    else:
        print(f"unlock:    ABSENT at {UNLOCK_PATH}")
        print("  state is encrypted-at-rest; operations will refuse until unlock is created")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse, sys
    p = argparse.ArgumentParser(prog="secure_state", description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true", help="generate new unlock key")
    g.add_argument("--init-force", dest="init_force", action="store_true",
                   help="overwrite existing unlock key (DESTRUCTIVE)")
    g.add_argument("--status", action="store_true", help="show unlock status")
    g.add_argument("--check", action="store_true", help="exit 0 if unlocked, 1 otherwise")
    args = p.parse_args(argv)
    if args.init or args.init_force:
        ns = argparse.Namespace(force=args.init_force)
        return cmd_init(ns)
    if args.status:
        return cmd_status(args)
    if args.check:
        return 0 if is_unlocked() else 1
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
