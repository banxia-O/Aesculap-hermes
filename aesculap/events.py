"""Detection events shared across the trigger architecture (PRD §2).

The three detectors (log watcher, liveness poll, full checkup) run in one
process and push DetectionEvents onto a single shared queue (PRD §2: "三套并入
一个进程，共享一条事件队列"). The daemon consumes from that queue.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class EventSource(enum.Enum):
    LOG_WATCHER = "log_watcher"
    LIVENESS = "liveness"
    FULL_CHECKUP = "full_checkup"


@dataclass
class DetectionEvent:
    """A raised signal that *might* indicate a fault.

    An event is not yet a confirmed fault — it still has to survive de-bounce
    (PRD §4) before any LLM is woken. `fingerprint` is a stable identity for the
    issue, used for de-bounce counting and de-dup/cooldown (PRD §12).
    `related_probes` names the Tier 0 probes the de-bounce step should re-run.
    """

    source: EventSource
    fingerprint: str
    summary: str
    evidence: str = ""
    related_probes: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
