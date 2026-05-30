"""Tier 0 probe interface (PRD §3).

A probe is a deterministic check that emits exactly one of OK / WARN / FAIL plus
evidence (relevant log lines, exit code, metric value). Probes MUST NOT call an
LLM (PRD §3) — they are cheap, run on every cycle, and stay silent when all OK.

Each built-in probe is registered by a string `type` so config can instantiate
it by name (PRD §3: probe definitions live in config and are user-extensible).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable


class ProbeStatus(enum.Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class ProbeResult:
    """One probe's verdict + evidence."""

    name: str
    status: ProbeStatus
    evidence: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status is ProbeStatus.FAIL


class Probe:
    """Base class for a deterministic Tier 0 probe.

    Subclasses implement :meth:`run`. The constructor takes the probe `name`
    (from config) and a `params` dict. Probes must never raise out of `run`;
    they convert their own internal errors into a FAIL/WARN result so one broken
    probe can't take down the detection cycle.
    """

    #: built-in registry id used in config `type:`
    type_id: str = ""

    def __init__(self, name: str, params: dict[str, Any] | None = None):
        self.name = name
        self.params = params or {}

    def run(self) -> ProbeResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # --- helpers for subclasses ------------------------------------------
    def ok(self, evidence: str = "", **metrics: Any) -> ProbeResult:
        return ProbeResult(self.name, ProbeStatus.OK, evidence, metrics)

    def warn(self, evidence: str = "", **metrics: Any) -> ProbeResult:
        return ProbeResult(self.name, ProbeStatus.WARN, evidence, metrics)

    def fail(self, evidence: str = "", **metrics: Any) -> ProbeResult:
        return ProbeResult(self.name, ProbeStatus.FAIL, evidence, metrics)

    def safe_run(self) -> ProbeResult:
        """Run the probe, converting any unexpected exception into a WARN.

        A probe that itself errors is a signal worth surfacing, but it must not
        be treated as a hard FAIL (which would trigger remediation of the wrong
        thing); WARN keeps it visible without escalating.
        """
        try:
            return self.run()
        except Exception as e:  # noqa: BLE001 - deliberate catch-all boundary
            return self.warn(f"probe raised {type(e).__name__}: {e}")


# Registry of built-in probe classes, keyed by `type_id`.
_REGISTRY: dict[str, type[Probe]] = {}


def register_probe(cls: type[Probe]) -> type[Probe]:
    """Class decorator: register a built-in probe by its `type_id`."""
    if not cls.type_id:
        raise ValueError(f"{cls.__name__} must set a non-empty type_id")
    if cls.type_id in _REGISTRY:
        raise ValueError(f"duplicate probe type_id: {cls.type_id}")
    _REGISTRY[cls.type_id] = cls
    return cls


def get_probe_class(type_id: str) -> type[Probe]:
    if type_id not in _REGISTRY:
        raise KeyError(
            f"unknown probe type {type_id!r}; known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[type_id]


def known_probe_types() -> list[str]:
    return sorted(_REGISTRY)
