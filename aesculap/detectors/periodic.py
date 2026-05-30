"""Periodic probe-driven detectors: liveness + full checkup (PRD §2).

Both run probes on a timer and emit a DetectionEvent for each FAIL:

- **Liveness** (every 1-5 min) — runs the liveness-relevant probe subset
  (process alive, heartbeat). Catches silent kills (OOM) that leave no log
  trace, which the log watcher cannot see (PRD §2).
- **Full checkup** (default 24h) — runs the WHOLE probe suite to catch the
  "broken without erroring" failures: hung-but-alive process, expired key never
  exercised, slowly filling disk; also self-checks daemon health (PRD §2).

A single FAIL becomes one event. When *several* probes FAIL at once, that is a
system-level signal handled by cascade protection downstream (PRD §7.3); the
detector just reports — it does not adjudicate.
"""

from __future__ import annotations

import hashlib
import threading
from queue import Queue

from aesculap.events import DetectionEvent, EventSource
from aesculap.probes.base import ProbeResult
from aesculap.probes.registry import ProbeSuite


def _fingerprint(prefix: str, probe_name: str) -> str:
    h = hashlib.sha1(f"{prefix}:{probe_name}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{h}"


def results_to_events(
    results: list[ProbeResult], source: EventSource, prefix: str
) -> list[DetectionEvent]:
    """Turn FAIL probe results into events (OK/WARN stay silent, PRD §3)."""
    events = []
    for r in results:
        if r.failed:
            events.append(
                DetectionEvent(
                    source=source,
                    fingerprint=_fingerprint(prefix, r.name),
                    summary=f"probe {r.name} FAILED",
                    evidence=r.evidence,
                    related_probes=[r.name],
                    details={"metrics": r.metrics},
                )
            )
    return events


class _PeriodicDetector:
    """Shared timer loop for probe-driven detectors."""

    source: EventSource
    prefix: str

    def __init__(
        self,
        queue: "Queue[DetectionEvent]",
        suite: ProbeSuite,
        interval_seconds: float,
        probe_names: list[str] | None = None,
    ):
        self.queue = queue
        self.suite = suite
        self.interval = interval_seconds
        # If probe_names is None, run the whole suite (full checkup); otherwise
        # the named subset (liveness).
        self.probe_names = probe_names
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_once(self) -> list[DetectionEvent]:
        """Run the configured probes once and enqueue FAIL events."""
        if self.probe_names is None:
            results = self.suite.run_all()
        else:
            results = self.suite.run_subset(self.probe_names)
        events = results_to_events(results, self.source, self.prefix)
        for e in events:
            self.queue.put(e)
        return events

    def _loop(self) -> None:
        # Run immediately on start, then on the interval.
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name=f"aesculap-{self.prefix}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


class LivenessDetector(_PeriodicDetector):
    """Polls liveness probes every 1-5 minutes (PRD §2)."""

    source = EventSource.LIVENESS
    prefix = "liveness"


class FullCheckupDetector(_PeriodicDetector):
    """Runs the whole probe suite on a long cycle, default 24h (PRD §2)."""

    source = EventSource.FULL_CHECKUP
    prefix = "full_checkup"

    def __init__(self, queue, suite, interval_seconds):
        super().__init__(queue, suite, interval_seconds, probe_names=None)
