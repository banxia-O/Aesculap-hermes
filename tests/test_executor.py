"""Remediation orchestrator tests (PRD §6.3, §7, §10.2)."""

from aesculap.audit.log import AuditLog
from aesculap.gate.escalation import EscalationLadder
from aesculap.events import DetectionEvent, EventSource
from aesculap.pipeline import PipelineOutcome
from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.remediate.executor import RemediationExecutor
from aesculap.remediate.selffix import FixAttemptResult
from aesculap.types import (
    BlastRadius,
    GateDecision,
    NeedsHumanReason,
    Route,
    TriageDecision,
)


def make_outcome(route, actions=None, before=None):
    triage = TriageDecision(
        diagnosis="d", blast_radius=BlastRadius.RESTART, reversible=True,
        route=route, actions=actions or ["restart hermes"],
    )
    gate = GateDecision(final_route=route, overridden=False, proposed_route=route,
                        reasons=["ok"])
    event = DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint="log:x",
                           summary="boom")
    return PipelineOutcome(event, triage, gate,
                           before or [ProbeResult("p", ProbeStatus.FAIL)], False)


class StubSelfFix:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def run(self, actions, before, state):
        r = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return r


class StubCodingAgent:
    def __init__(self, success, avail=True):
        self._success = success
        self._avail = avail
        self.calls = 0

    def available(self):
        return self._avail

    def run(self, prompt, before):
        from aesculap.remediate.coding_agent import CodingAgentResult
        self.calls += 1
        if self._success:
            return CodingAgentResult(True, None, "fixed", Route.CODING_AGENT, "abc")
        return CodingAgentResult(False, None, "failed", Route.HUMAN)


def audit(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def test_observe_mode_never_acts(tmp_path):
    sf = StubSelfFix([])
    ex = RemediationExecutor("observe", audit(tmp_path), selffix=sf)
    res = ex.remediate(make_outcome(Route.SELF_FIX))
    assert not res.success
    assert sf.calls == 0
    assert "observe" in res.reason


def test_self_fix_success(tmp_path):
    sf = StubSelfFix([
        FixAttemptResult(True, None, "fixed", Route.SELF_FIX, 1),
    ])
    ex = RemediationExecutor("fix", audit(tmp_path), selffix=sf)
    res = ex.remediate(make_outcome(Route.SELF_FIX))
    assert res.success
    assert res.final_route is Route.SELF_FIX


def test_self_fix_retries_then_escalates_to_coding_agent(tmp_path):
    sf = StubSelfFix([
        FixAttemptResult(False, None, "v1", Route.SELF_FIX, 1),
        FixAttemptResult(False, None, "v2", Route.SELF_FIX, 2),
        FixAttemptResult(False, None, "v3", Route.CODING_AGENT, 3),
    ])
    ca = StubCodingAgent(success=True)
    ex = RemediationExecutor("fix", audit(tmp_path), selffix=sf, coding_agent=ca)
    res = ex.remediate(make_outcome(Route.SELF_FIX))
    assert sf.calls == 3
    assert ca.calls == 1
    assert res.success
    assert res.final_route is Route.CODING_AGENT


def test_coding_agent_failure_escalates_to_human(tmp_path):
    sf = StubSelfFix([FixAttemptResult(False, None, "v", Route.CODING_AGENT, 3)])
    ca = StubCodingAgent(success=False)
    notified = []
    ex = RemediationExecutor("fix", audit(tmp_path), selffix=sf, coding_agent=ca,
                             notify_fn=lambda o, r: notified.append(r))
    res = ex.remediate(make_outcome(Route.SELF_FIX))
    assert res.final_route is Route.HUMAN
    assert not res.success
    assert notified  # human was notified


def test_human_route_notifies(tmp_path):
    notified = []
    ex = RemediationExecutor("fix", audit(tmp_path),
                             notify_fn=lambda o, r: notified.append(r))
    res = ex.remediate(make_outcome(Route.HUMAN))
    assert res.final_route is Route.HUMAN
    assert len(notified) == 1


def test_report_only_no_action(tmp_path):
    ex = RemediationExecutor("fix", audit(tmp_path))
    res = ex.remediate(make_outcome(Route.REPORT_ONLY))
    assert res.success
    assert res.final_route is Route.REPORT_ONLY


def test_coding_agent_unavailable_to_human(tmp_path):
    sf = StubSelfFix([FixAttemptResult(False, None, "v", Route.CODING_AGENT, 3)])
    ca = StubCodingAgent(success=True, avail=False)
    ex = RemediationExecutor("fix", audit(tmp_path), selffix=sf, coding_agent=ca)
    res = ex.remediate(make_outcome(Route.SELF_FIX))
    assert res.final_route is Route.HUMAN
    assert ca.calls == 0


def test_audit_records_escalation(tmp_path):
    a = audit(tmp_path)
    ex = RemediationExecutor("fix", a)
    ex.remediate(make_outcome(Route.HUMAN))
    events = [r["event"] for r in a.read_all()]
    assert "escalate_human" in events
