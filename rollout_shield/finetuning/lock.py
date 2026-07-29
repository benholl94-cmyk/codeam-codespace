"""Cross-process lock used during a finetuning run.

Mirrors the pattern in ``rollout_shield.webhook_delivery.dispatcher`` —
``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. The lock
lives at ``<state>/finetuning/.lock`` and is per-state-root.

The lock is process-cooperative, not security — anyone who has the
file descriptor could clobber it. Its only purpose is to prevent two
``finetune run`` invocations from corrupting each other's run record.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType


def _lock_path(state_root: Path) -> Path:
    return state_root / "finetuning" / ".lock"


class FinetuneLock:
    """Context manager — ``with FinetuneLock(state_root): ...``."""

    def __init__(self, state_root: Path,
                 timeout_seconds: float = 0.0,
                 poll_interval: float = 0.05) -> None:
        self._path = _lock_path(state_root)
        self._timeout = timeout_seconds
        self._poll = poll_interval
        self._fd: int | None = None

    def _try_lock(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if os.name == "nt":
                # Windows: msvcrt.locking(fd, mode, nbytes) — mode 2 = LK_LOCK
                import msvcrt
                # try one byte at position 0
                try:
                    msvcrt.locking(fd, 2, 1)
                    self._fd = fd
                    return True
                except OSError:
                    os.close(fd)
                    return False
            else:
                # POSIX: fcntl.flock with LOCK_SH | LOCK_NB
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return True
                except OSError:
                    os.close(fd)
                    return False
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            return False

    def acquire(self) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            if self._try_lock():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"could not acquire finetuning lock at {self._path} "
                    f"within {self._timeout}s"
                )
            time.sleep(self._poll)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(self._fd, 8, 1)  # LK_UNLCK
                except OSError:  # pragma: no cover — best-effort unlock
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:  # pragma: no cover
                    pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> FinetuneLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.release()


def locked(state_root: Path) -> Iterator[FinetuneLock]:
    """``with locked(state_root) as l: ...`` convenience helper."""
    lk = FinetuneLock(state_root)
    lk.acquire()
    try:
        yield lk
    finally:
        lk.release()


__all__ = ["FinetuneLock", "locked"]
