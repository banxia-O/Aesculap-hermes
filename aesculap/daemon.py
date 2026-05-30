"""Aesculap daemon — single process, three detectors, one event queue (PRD §2).

Wires the trigger architecture together:

- starts the log watcher, liveness poll, and full checkup on one shared queue
- consumes events and runs them through de-bounce (PRD §4)
- a confirmed (persistent) fault is handed to the `on_confirmed` callback

Phase 2 stops at "confirmed fault ready for Tier 1". The triage layer (Phase 3)
and remediation (Phase 4) plug into `on_confirmed`. Tier 1+ work is guarded by
the concurrency lock (PRD §12) so a long-running coding_agent run can't be
double-triggered.

systemd manages the process lifecycle (PRD §2): if the daemon crashes, systemd
restarts it; Aesculap never restarts itself (§9.3).
"""

from __future__ import annotations

import threading
from queue import Empty, Queue
from typing import Callable

from aesculap.audit.log import AuditLog
from aesculap.config import Config
from aesculap.debounce import Debouncer
from aesculap.detectors.log_watcher import LogWatcher
from aesculap.detectors.periodic import FullCheckupDetector, LivenessDetector
from aesculap.events import DetectionEvent
from aesculap.lockfile import FileLock, LockHeld
from aesculap.probes.registry import ProbeSuite

# Probe types that count as "liveness" for the fast poll (PRD §2). The rest run
# only in the full checkup.
_LIVENESS_PROBE_TYPES = {"process_alive", "heartbeat_fresh"}

# Callback invoked once a fault survives de-bounce. Returns nothing; the triage
# + remediation pipeline (later phases) lives behind it.
ConfirmedHandler = Callable[[DetectionEvent], None]


class Daemon:
    def __init__(
        self,
        config: Config,
        audit: AuditLog,
        on_confirmed: ConfirmedHandler | None = None,
    ):
        self.config = config
        self.audit = audit
        self.queue: "Queue[DetectionEvent]" = Queue()
        self.suite = ProbeSuite.from_config(config.probes)
        self.debouncer = Debouncer(
            consecutive_threshold=config.debounce.consecutive_threshold,
            recheck_seconds=config.debounce.recheck_seconds,
        )
        self.on_confirmed = on_confirmed or self._default_confirmed
        self._stop = threading.Event()
        self._lock = FileLock(self._lock_path())

        liveness_names = [
            pc.name for pc in config.probes
            if pc.enabled and pc.type in _LIVENESS_PROBE_TYPES
        ]
        d = config.detectors
        self.log_watcher = LogWatcher(
            self.queue, d.log_paths, d.error_patterns,
            related_probes=liveness_names,
        )
        self.liveness = LivenessDetector(
            self.queue, self.suite, d.liveness_interval_seconds,
            probe_names=liveness_names,
        )
        self.full_checkup = FullCheckupDetector(
            self.queue, self.suite, d.full_checkup_interval_seconds,
        )

    def _lock_path(self) -> str:
        base = self.config.state_dir or "/tmp"
        return f"{base.rstrip('/')}/aesculap.lock"

    # --- event handling ---------------------------------------------------
    def _default_confirmed(self, event: DetectionEvent) -> None:
        """Phase 2 default: record the confirmed fault; later phases triage it."""
        self.audit.record(
            "fault_confirmed",
            fingerprint=event.fingerprint,
            source=event.source,
            summary=event.summary,
            evidence=event.evidence,
        )

    def handle_event(self, event: DetectionEvent) -> bool:
        """Run one event through de-bounce; dispatch if confirmed.

        Returns True if the event was confirmed (escalated). In `observe` mode
        we still detect + confirm + audit, but never act (PRD §10.2) — acting is
        a later-phase concern gated on mode there.
        """
        self.audit.record(
            "detection",
            fingerprint=event.fingerprint,
            source=event.source,
            summary=event.summary,
        )
        confirmed = self.debouncer.observe(event)
        if not confirmed:
            return False
        self.on_confirmed(event)
        return True

    def drain_once(self, timeout: float = 0.1) -> int:
        """Process all currently-queued events. Returns count handled."""
        handled = 0
        while True:
            try:
                event = self.queue.get(timeout=timeout)
            except Empty:
                break
            self.handle_event(event)
            handled += 1
            self.queue.task_done()
        return handled

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if not self.config.enabled:
            self.audit.record("daemon_disabled", reason="master switch off (§10.1)")
            return
        try:
            self._lock.acquire()
        except LockHeld:
            self.audit.record("daemon_lock_held", path=self._lock_path())
            raise
        self.audit.record("daemon_start", mode=self.config.mode)
        self.log_watcher.start()
        self.liveness.start()
        self.full_checkup.start()

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop.is_set():
                self.drain_once(timeout=0.5)
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.log_watcher.stop()
        self.liveness.stop()
        self.full_checkup.stop()
        if self._lock.held:
            self.audit.record("daemon_stop")
            self._lock.release()
