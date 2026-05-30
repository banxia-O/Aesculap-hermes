"""De-bounce tests (PRD §4): absorb transients, escalate persistent faults."""

from aesculap.debounce import Debouncer
from aesculap.events import DetectionEvent, EventSource


def ev(fp="log:abc"):
    return DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint=fp,
                          summary="x")


def test_first_occurrence_does_not_escalate():
    d = Debouncer(consecutive_threshold=2, recheck_seconds=60)
    assert d.observe(ev(), now=1000.0) is False


def test_second_consecutive_escalates():
    d = Debouncer(consecutive_threshold=2, recheck_seconds=60)
    d.observe(ev(), now=1000.0)
    assert d.observe(ev(), now=1001.0) is True


def test_persistence_escalates_even_below_count():
    d = Debouncer(consecutive_threshold=5, recheck_seconds=60)
    d.observe(ev(), now=1000.0)
    # only 2nd occurrence (below count of 5) but >60s elapsed -> escalate
    assert d.observe(ev(), now=1100.0) is True


def test_threshold_one_escalates_immediately():
    d = Debouncer(consecutive_threshold=1, recheck_seconds=60)
    assert d.observe(ev(), now=1000.0) is True


def test_distinct_fingerprints_tracked_separately():
    d = Debouncer(consecutive_threshold=2, recheck_seconds=60)
    assert d.observe(ev("a"), now=1.0) is False
    assert d.observe(ev("b"), now=2.0) is False  # different fp, still first


def test_clear_resets():
    d = Debouncer(consecutive_threshold=2, recheck_seconds=60)
    d.observe(ev(), now=1.0)
    d.clear("log:abc")
    assert not d.is_tracking("log:abc")
    assert d.observe(ev(), now=2.0) is False  # back to first occurrence
