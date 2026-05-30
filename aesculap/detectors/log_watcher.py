"""Log watcher — the primary detector (PRD §2).

Self-built ``tail -F`` over stdlib: follows one or more log files, matches
configured error patterns line-by-line in real time, and pushes a
DetectionEvent per match. Handles the two ways a followed file can change out
from under us:

- **rotation** (file replaced; inode changes) — reopen the path
- **truncation** (file shrinks; e.g. logrotate copytruncate) — seek to 0

Implemented as a poll loop (default 0.5s) rather than inotify to stay
dependency-free and portable; cost is near-zero per PRD §2.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from queue import Queue

from aesculap.events import DetectionEvent, EventSource


def _fingerprint(path: str, line: str) -> str:
    """Stable id for a log-derived issue.

    We key on the file plus a normalized form of the line (digits/hex stripped)
    so repeated occurrences of "the same" error — differing only in timestamps,
    pids, addresses — collapse to one fingerprint for de-bounce/de-dup.
    """
    norm = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", line)
    h = hashlib.sha1(f"{path}:{norm}".encode("utf-8")).hexdigest()[:12]
    return f"log:{h}"


class _FollowedFile:
    """Tracks read position + inode for one followed file."""

    def __init__(self, path: Path, from_end: bool = True):
        self.path = path
        self._fh = None
        self._inode: int | None = None
        self._from_end = from_end

    def _open(self) -> bool:
        try:
            fh = self.path.open("r", encoding="utf-8", errors="replace")
            st = os.fstat(fh.fileno())
        except OSError:
            return False
        if self._from_end:
            fh.seek(0, os.SEEK_END)
            self._from_end = False  # only skip-to-end on first open
        self._fh = fh
        self._inode = st.st_ino
        return True

    def read_new_lines(self) -> list[str]:
        """Return any lines appended since the last read; handle rotation."""
        if self._fh is None and not self._open():
            return []
        assert self._fh is not None
        # Detect rotation (inode changed) or truncation (size < position).
        try:
            disk = self.path.stat()
        except OSError:
            # File vanished (mid-rotation); drop handle and retry next tick.
            self._close()
            return []
        if disk.st_ino != self._inode:
            # Rotated: drain the rest of the old handle, then reopen the new.
            tail = self._fh.read().splitlines()
            self._close()
            self._open()
            new = self._fh.read().splitlines() if self._fh else []
            return tail + new
        if disk.st_size < self._fh.tell():
            # Truncated in place (copytruncate): rewind to start.
            self._fh.seek(0)
        return self._fh.read().splitlines()

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._inode = None


class LogWatcher:
    """Follows log files and emits DetectionEvents on error-pattern matches."""

    def __init__(
        self,
        queue: "Queue[DetectionEvent]",
        log_paths: list[str],
        error_patterns: list[str],
        related_probes: list[str] | None = None,
        poll_interval: float = 0.5,
        from_end: bool = True,
    ):
        self.queue = queue
        self.patterns = [re.compile(p) for p in error_patterns]
        self.related_probes = related_probes or []
        self.poll_interval = poll_interval
        self._files = [_FollowedFile(Path(p), from_end=from_end) for p in log_paths]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _match(self, line: str) -> bool:
        return any(rx.search(line) for rx in self.patterns)

    def poll_once(self) -> int:
        """One scan pass over all files; emit events. Returns # events raised.

        Exposed for testing without spinning a thread.
        """
        raised = 0
        for f in self._files:
            for line in f.read_new_lines():
                if not line.strip() or not self._match(line):
                    continue
                self.queue.put(
                    DetectionEvent(
                        source=EventSource.LOG_WATCHER,
                        fingerprint=_fingerprint(str(f.path), line),
                        summary=f"log error pattern matched in {f.path.name}",
                        evidence=line,
                        related_probes=list(self.related_probes),
                        details={"path": str(f.path)},
                    )
                )
                raised += 1
        return raised

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.poll_interval)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="aesculap-log-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for f in self._files:
            f._close()
