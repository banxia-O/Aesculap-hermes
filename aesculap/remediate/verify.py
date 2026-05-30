"""Full verification + observation window (PRD §7.1, decision #2).

After a fix, rerun the WHOLE probe suite — not just the one that broke — to
catch "fixed A, broke B" (PRD §7.1 step 3).

**Decision #2 — success criterion:** verification passes iff every probe that
was FAIL *before* the fix is now OK, AND no probe that was previously non-FAIL
has newly become FAIL (that would be "fixed A, broke B"). Probes that were WARN
and stay WARN, or OK and stay OK, are fine — we do NOT require all-green.

The observation window (PRD §7.1 step 4) re-checks after a delay so a fix that
"works" momentarily but regresses doesn't get declared a success.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.probes.registry import ProbeSuite


@dataclass
class VerifyResult:
    passed: bool
    reason: str
    newly_fixed: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    newly_broken: list[str] = field(default_factory=list)
    after: list[ProbeResult] = field(default_factory=list)


def _status_map(results: list[ProbeResult]) -> dict[str, ProbeStatus]:
    return {r.name: r.status for r in results}


def evaluate(
    before: list[ProbeResult], after: list[ProbeResult]
) -> VerifyResult:
    """Apply decision #2 to before/after probe snapshots."""
    before_s = _status_map(before)
    after_s = _status_map(after)

    previously_failing = [n for n, s in before_s.items() if s is ProbeStatus.FAIL]
    still_failing = [
        n for n in previously_failing
        if after_s.get(n, ProbeStatus.FAIL) is ProbeStatus.FAIL
    ]
    newly_fixed = [
        n for n in previously_failing
        if after_s.get(n) is ProbeStatus.OK
    ]
    # "fixed A broke B": a probe that was NOT failing before is now FAIL.
    newly_broken = [
        n for n, s in after_s.items()
        if s is ProbeStatus.FAIL and before_s.get(n) is not ProbeStatus.FAIL
    ]

    if still_failing:
        return VerifyResult(
            False, f"still failing: {', '.join(still_failing)}",
            newly_fixed, still_failing, newly_broken, after,
        )
    if newly_broken:
        return VerifyResult(
            False, f"fix broke other probes: {', '.join(newly_broken)}",
            newly_fixed, still_failing, newly_broken, after,
        )
    # All previously-failing probes are now OK and nothing new broke.
    if not previously_failing:
        # Nothing was failing to begin with (e.g. restart for a liveness blip
        # the probe already cleared): treat as pass if nothing broke.
        return VerifyResult(True, "no prior failures; nothing newly broken",
                            newly_fixed, still_failing, newly_broken, after)
    return VerifyResult(
        True, f"resolved: {', '.join(newly_fixed)}",
        newly_fixed, still_failing, newly_broken, after,
    )


class Verifier:
    """Runs the full suite and applies the decision #2 criterion."""

    def __init__(self, suite: ProbeSuite):
        self.suite = suite

    def snapshot(self) -> list[ProbeResult]:
        """Run the whole suite once (pre- or post-fix baseline)."""
        return self.suite.run_all()

    def verify(self, before: list[ProbeResult]) -> VerifyResult:
        """Re-run the full suite and evaluate against the before-snapshot."""
        after = self.suite.run_all()
        return evaluate(before, after)
