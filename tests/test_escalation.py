"""Escalation ladder tests (PRD §6.3, §7.2): only up, never down."""

from aesculap.gate.escalation import EscalationLadder, EscalationState
from aesculap.types import Route


def test_self_fix_within_budget_stays():
    ladder = EscalationLadder(retry_budget=3, coding_agent_available=True)
    st = EscalationState()
    assert ladder.next_after_self_fix_failure(st) is Route.SELF_FIX  # attempt 1
    assert ladder.next_after_self_fix_failure(st) is Route.SELF_FIX  # attempt 2
    assert st.self_fix_attempts == 2


def test_self_fix_budget_exhausted_escalates_to_coding_agent():
    ladder = EscalationLadder(retry_budget=3, coding_agent_available=True)
    st = EscalationState()
    ladder.next_after_self_fix_failure(st)  # 1
    ladder.next_after_self_fix_failure(st)  # 2
    nxt = ladder.next_after_self_fix_failure(st)  # 3 -> exhausted
    assert nxt is Route.CODING_AGENT


def test_budget_exhausted_to_human_when_no_agent():
    ladder = EscalationLadder(retry_budget=1, coding_agent_available=False)
    st = EscalationState()
    assert ladder.next_after_self_fix_failure(st) is Route.HUMAN


def test_coding_agent_failure_escalates_to_human():
    ladder = EscalationLadder(coding_agent_available=True)
    st = EscalationState(current=Route.CODING_AGENT)
    assert ladder.next_after_coding_agent_failure(st) is Route.HUMAN


def test_never_steps_down():
    ladder = EscalationLadder()
    st = EscalationState(current=Route.HUMAN)
    # asking to clamp to a lower rung keeps it at human
    assert ladder.clamp_up(st, Route.SELF_FIX) is Route.HUMAN
    assert ladder.clamp_up(st, Route.CODING_AGENT) is Route.HUMAN


def test_clamp_up_moves_up():
    ladder = EscalationLadder()
    st = EscalationState(current=Route.SELF_FIX)
    assert ladder.clamp_up(st, Route.CODING_AGENT) is Route.CODING_AGENT
    assert st.current is Route.CODING_AGENT


def test_report_only_passes_through_clamp():
    ladder = EscalationLadder()
    st = EscalationState(current=Route.SELF_FIX)
    assert ladder.clamp_up(st, Route.REPORT_ONLY) is Route.REPORT_ONLY
    # current (a ladder rung) is unchanged by an off-ladder target
    assert st.current is Route.SELF_FIX
