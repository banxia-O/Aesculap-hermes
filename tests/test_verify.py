"""Verification tests (PRD §7.1, decision #2).

Decision #2: pass iff every previously-FAIL probe is now OK and nothing
previously-non-FAIL newly broke. NOT all-green.
"""

from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.remediate.verify import evaluate


def r(name, status):
    return ProbeResult(name, status)


OK = ProbeStatus.OK
WARN = ProbeStatus.WARN
FAIL = ProbeStatus.FAIL


def test_failed_probe_now_ok_passes():
    before = [r("p", FAIL)]
    after = [r("p", OK)]
    res = evaluate(before, after)
    assert res.passed
    assert res.newly_fixed == ["p"]


def test_still_failing_fails():
    res = evaluate([r("p", FAIL)], [r("p", FAIL)])
    assert not res.passed
    assert res.still_failing == ["p"]


def test_other_probes_keep_status_not_required_green():
    """A WARN probe staying WARN must not block success (not all-green)."""
    before = [r("broken", FAIL), r("warn", WARN)]
    after = [r("broken", OK), r("warn", WARN)]
    res = evaluate(before, after)
    assert res.passed


def test_fixed_a_broke_b_fails():
    before = [r("a", FAIL), r("b", OK)]
    after = [r("a", OK), r("b", FAIL)]
    res = evaluate(before, after)
    assert not res.passed
    assert res.newly_broken == ["b"]


def test_newly_broke_warn_to_fail():
    before = [r("a", FAIL), r("b", WARN)]
    after = [r("a", OK), r("b", FAIL)]
    res = evaluate(before, after)
    assert not res.passed
    assert "b" in res.newly_broken


def test_no_prior_failures_passes_if_nothing_breaks():
    # e.g. restart cleared the issue before the post-probe ran
    before = [r("a", OK)]
    after = [r("a", OK)]
    assert evaluate(before, after).passed


def test_no_prior_failures_but_new_break_fails():
    before = [r("a", OK)]
    after = [r("a", FAIL)]
    assert not evaluate(before, after).passed
