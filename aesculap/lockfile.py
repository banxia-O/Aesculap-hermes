"""Concurrency lock (PRD §12).

A single-holder lockfile prevents the daemon from launching a second
remediation while a previous one (a coding_agent run can take minutes) is still
in flight. Uses ``fcntl.flock`` with an exclusive non-blocking lock so the lock
is released automatically if the holding process dies (no stale PID files to
reap).
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class LockHeld(RuntimeError):
    """Raised when the lock is already held by another holder."""


class FileLock:
    """An advisory exclusive lock backed by flock(2)."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise LockHeld(f"lock already held: {self.path}") from e
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
