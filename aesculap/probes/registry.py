"""Probe registry: instantiate probes from config and run the suite (PRD §3).

Supports running the full suite (PRD §7.1 "rerun the WHOLE set, not just the
one that broke") or a relevant subset (PRD §4 de-bounce step 1: "run the
related Tier 0 probes").
"""

from __future__ import annotations

# Importing builtin registers all built-in probe classes as a side effect.
from aesculap.config import ProbeConfig
from aesculap.probes import builtin as _builtin  # noqa: F401
from aesculap.probes.base import Probe, ProbeResult, get_probe_class


def build_probes(probe_configs: list[ProbeConfig]) -> list[Probe]:
    """Instantiate enabled probes from config, preserving order."""
    probes: list[Probe] = []
    for pc in probe_configs:
        if not pc.enabled:
            continue
        cls = get_probe_class(pc.type)
        probes.append(cls(name=pc.name, params=pc.params))
    return probes


class ProbeSuite:
    """A built, runnable collection of probes."""

    def __init__(self, probes: list[Probe]):
        self.probes = probes
        self._by_name = {p.name: p for p in probes}

    @classmethod
    def from_config(cls, probe_configs: list[ProbeConfig]) -> "ProbeSuite":
        return cls(build_probes(probe_configs))

    def run_all(self) -> list[ProbeResult]:
        """Run every probe (full verification, §7.1). Never raises."""
        return [p.safe_run() for p in self.probes]

    def run_subset(self, names: list[str]) -> list[ProbeResult]:
        """Run only the named probes (de-bounce relevant subset, §4)."""
        results = []
        for name in names:
            probe = self._by_name.get(name)
            if probe is not None:
                results.append(probe.safe_run())
        return results

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.probes]
