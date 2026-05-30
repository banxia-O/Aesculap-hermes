"""Aesculap daemon — single process, three detectors, one event queue (PRD §2).

Wires the trigger architecture together:

- starts the log watcher, liveness poll, and full checkup on one shared queue
- consumes events and runs them through de-bounce (PRD §4)
- a confirmed (persistent) fault is handed to the `on_confirmed` callback

When a triage provider is configured, the daemon builds a Pipeline (Phase 3:
triage -> code gate, PRD §5/§6) and uses it as the confirmed-fault handler.
Remediation (Phase 4) plugs in after the gate. Tier 1+ work is guarded by the
concurrency lock (PRD §12) so a long-running coding_agent run can't be
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
        self.pipeline = None
        self.remediation = None
        self.notifier = None
        if on_confirmed is not None:
            self.on_confirmed = on_confirmed
        else:
            self.pipeline = self._maybe_build_pipeline()
            self.on_confirmed = (
                self._pipeline_confirmed
                if self.pipeline is not None
                else self._default_confirmed
            )
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

    def _maybe_build_pipeline(self):
        """Build the triage->gate pipeline + remediation executor if a triage
        provider is configured.

        Kept import-local so the daemon (and Phase 2 tests) don't require any
        provider SDK unless triage is actually configured.
        """
        if not self.config.triage.provider:
            return None
        from aesculap.gate.blast_radius import BlastRadiusGate
        from aesculap.gate.escalation import EscalationLadder
        from aesculap.gate.scope import ScopeGate
        from aesculap.gate.tripwires import TripwireGate
        from aesculap.install.capabilities import detect_capabilities
        from aesculap.llm.providers import provider_from_triage
        from aesculap.pipeline import Pipeline
        from aesculap.remediate.backup import FileBackupManager, GitBackupManager
        from aesculap.remediate.coding_agent import CodingAgentExecutor
        from aesculap.remediate.executor import RemediationExecutor
        from aesculap.remediate.selffix import SelfFixExecutor
        from aesculap.remediate.verify import Verifier
        from aesculap.notify.dedup import NotificationDeduper
        from aesculap.notify.notifier import Notifier
        from aesculap.triage.triager import Triager

        caps = detect_capabilities(self.config)
        scope = ScopeGate(self.config.scope, self.config.aesculap_home)
        gate = BlastRadiusGate(TripwireGate(scope), caps.coding_agent_available)
        triager = Triager(provider_from_triage(self.config.triage))

        state_dir = self.config.state_dir or "/tmp"
        verifier = Verifier(self.suite)
        ladder = EscalationLadder(
            retry_budget=self.config.selffix.retry_budget,
            coding_agent_available=caps.coding_agent_available,
        )
        selffix = SelfFixExecutor(
            verifier,
            FileBackupManager(f"{state_dir.rstrip('/')}/backups"),
            ladder,
            observe_window_seconds=self.config.selffix.observe_window_seconds,
            scope=scope,
        )
        coding_agent = None
        if caps.coding_agent_available:
            project = self.config.scope.project_root or "."
            coding_agent = CodingAgentExecutor(
                tool=caps.coding_agents[0],
                verifier=verifier,
                git=GitBackupManager(project),
                command_template=self.config.coding_agent.command_template,
            )
        # Notifier (§8.3, §11): only built if a channel is configured; the
        # deduper suppresses re-spam of pending issues (§12).
        self.notifier = None
        if self.config.notify.command_template:
            self.notifier = Notifier(
                self.config.notify.command_template,
                deduper=NotificationDeduper(
                    f"{state_dir.rstrip('/')}/open_issues.json",
                    cooldown_seconds=self.config.notify.cooldown_seconds,
                ),
            )
        self.remediation = RemediationExecutor(
            mode=self.config.mode,
            audit=self.audit,
            selffix=selffix,
            coding_agent=coding_agent,
            ladder=ladder,
            notify_fn=self._notify_hook,
        )
        return Pipeline(self.suite, triager, gate, self.audit)

    def _notify_hook(self, outcome, reason) -> None:
        """Send the actionable human notification (§8.3) via the gateway.

        Builds the four-part message from the outcome and routes it through the
        notifier (de-dup + cooldown + key-safety). If no channel is configured,
        records the pending escalation for audit instead of sending.
        """
        from aesculap.notify.message import build_message

        event = outcome.event
        triage = outcome.triage
        if self.notifier is None:
            self.audit.record(
                "notify_human_pending", fingerprint=event.fingerprint,
                reason=reason,
            )
            return
        triggering_probe = (
            event.related_probes[0] if event.related_probes else ""
        )
        message = build_message(
            fault_summary=event.summary,
            triggering_probe=triggering_probe,
            evidence=event.evidence,
            diagnosis=triage.diagnosis,
            attempts=[reason] if reason else [],
            needs_human_reason=outcome.gate.needs_human_reason,
        )
        result = self.notifier.notify(event.fingerprint, message)
        self.audit.record(
            "notify_human", fingerprint=event.fingerprint,
            sent=result.sent, suppressed=result.suppressed, reason=result.reason,
        )

    # --- event handling ---------------------------------------------------
    def _default_confirmed(self, event: DetectionEvent) -> None:
        """No triage configured: record the confirmed fault (audit-only)."""
        self.audit.record(
            "fault_confirmed",
            fingerprint=event.fingerprint,
            source=event.source,
            summary=event.summary,
            evidence=event.evidence,
        )

    def _pipeline_confirmed(self, event: DetectionEvent) -> None:
        """Run the confirmed fault through triage -> gate -> remediation.

        Triage proposes (§5); the code gate decides (§6); remediation executes
        the approved route, gated on `mode == fix` (§10.2). Every step is
        audited (§13).
        """
        assert self.pipeline is not None
        outcome = self.pipeline.process(event)
        self.remediation.remediate(outcome)

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
