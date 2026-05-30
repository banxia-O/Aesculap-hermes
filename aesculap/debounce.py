"""De-bounce / escalation gate before waking the LLM (PRD §4).

An error event entering the queue does NOT immediately wake the LLM. First a
cheap confirmation:

1. Re-run the related Tier 0 probes.
2. Wait + re-check (default 60s): is the error still there? Did Hermes recover
   on its own?

Only a *persistent* fault escalates to Tier 1. Transient blips (occasional 503,
network flap, Hermes self-heal) are absorbed here without burning tokens. The
threshold (consecutive count / duration) is configurable (PRD §4, §10.3),
default: 2 consecutive OR persisting > 60s.

This module is the deterministic confirmation policy; the actual "wait" and
"re-run probes" are injected so the daemon controls timing and the tests stay
fast.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from aesculap.events import DetectionEvent


@dataclass
class _Tracker:
    """Per-fingerprint de-bounce state."""

    first_seen: float
    last_seen: float
    consecutive: int = 1
    history: list[float] = field(default_factory=list)


class Debouncer:
    """Decides whether a repeated/persistent event should escalate to Tier 1."""

    def __init__(self, consecutive_threshold: int = 2, recheck_seconds: float = 60):
        self.consecutive_threshold = max(1, consecutive_threshold)
        self.recheck_seconds = recheck_seconds
        self._trackers: dict[str, _Tracker] = {}

    def observe(self, event: DetectionEvent, now: float | None = None) -> bool:
        """Record an event occurrence; return True if it should escalate.

        Escalates when EITHER the consecutive-occurrence count reaches the
        threshold OR the fault has now persisted longer than `recheck_seconds`
        since first seen (PRD §4 default: 2 consecutive or >60s).
        """
        now = time.time() if now is None else now
        tr = self._trackers.get(event.fingerprint)
        if tr is None:
            self._trackers[event.fingerprint] = _Tracker(
                first_seen=now, last_seen=now, history=[now]
            )
            # First sighting: never escalate immediately (absorb transients).
            return self.consecutive_threshold <= 1
        tr.consecutive += 1
        tr.last_seen = now
        tr.history.append(now)
        persisted = (now - tr.first_seen) >= self.recheck_seconds
        enough = tr.consecutive >= self.consecutive_threshold
        return enough or persisted

    def clear(self, fingerprint: str) -> None:
        """Forget a fingerprint once its fault is resolved (recovered)."""
        self._trackers.pop(fingerprint, None)

    def is_tracking(self, fingerprint: str) -> bool:
        return fingerprint in self._trackers
