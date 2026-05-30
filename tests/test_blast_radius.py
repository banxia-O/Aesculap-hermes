"""Blast-radius code gate tests (PRD §6.2, §1).

The keystone safety property: an over-confident or adversarial LLM cannot get a
dangerous action executed. The code overrides the LLM's route.
"""

import pytest

from aesculap.config import ScopeConfig
from aesculap.gate.blast_radius import BlastRadiusGate
from aesculap.gate.scope import ScopeGate
from aesculap.gate.tripwires import TripwireGate
from aesculap.types import (
    ActionKind,
    BlastRadius,
    NeedsHumanReason,
    ProposedAction,
    Route,
    TriageDecision,
)


@pytest.fixture
def gate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    tw = TripwireGate(scope)
    return BlastRadiusGate(tw, coding_agent_available=True), project


def triage(route, radius, reversible=True, confidence=0.99):
    return TriageDecision(
        diagnosis="d",
        blast_radius=radius,
        reversible=reversible,
        route=route,
        confidence=confidence,
    )


def test_self_fix_single_file_allowed(gate):
    g, project = gate
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / "a.py"))]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.SINGLE_FILE), actions)
    assert d.final_route is Route.SELF_FIX
    assert not d.overridden


def test_self_fix_restart_allowed(gate):
    g, _ = gate
    actions = [ProposedAction(ActionKind.RESTART_PROCESS, description="restart")]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.RESTART), actions)
    assert d.final_route is Route.SELF_FIX


@pytest.mark.parametrize("radius", [BlastRadius.MULTI_FILE, BlastRadius.INFRA,
                                    BlastRadius.UNKNOWN])
def test_self_fix_forbidden_radius_downgraded(gate, radius):
    g, project = gate
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / "a.py"))]
    d = g.decide(triage(Route.SELF_FIX, radius), actions)
    assert d.final_route is Route.CODING_AGENT
    assert d.overridden


def test_self_fix_irreversible_downgraded(gate):
    g, project = gate
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / "a.py"))]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.SINGLE_FILE, reversible=False),
                 actions)
    assert d.final_route is Route.CODING_AGENT
    assert d.overridden


def test_self_fix_forbidden_radius_to_human_when_no_agent(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    g = BlastRadiusGate(TripwireGate(scope), coding_agent_available=False)
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / "a.py"))]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.INFRA), actions)
    assert d.final_route is Route.HUMAN


def test_tripwire_beats_self_fix_even_single_file(gate):
    """Adversarial: LLM says self_fix / single_file / reversible, but the
    action is `rm -rf`. Tripwire must force human."""
    g, _ = gate
    actions = [ProposedAction(ActionKind.RUN_COMMAND, command="rm -rf /home")]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.SINGLE_FILE), actions)
    assert d.final_route is Route.HUMAN
    assert d.overridden


def test_tripwire_on_credential_write(gate):
    g, project = gate
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / ".env"))]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.SINGLE_FILE), actions)
    assert d.final_route is Route.HUMAN


def test_confidence_is_never_a_gate(gate):
    """High confidence must not rescue a forbidden radius (PRD §5.2)."""
    g, project = gate
    actions = [ProposedAction(ActionKind.WRITE_FILE, path=str(project / "a.py"))]
    d = g.decide(triage(Route.SELF_FIX, BlastRadius.INFRA, confidence=1.0), actions)
    assert d.final_route is not Route.SELF_FIX


def test_coding_agent_unavailable_degrades_to_human(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    g = BlastRadiusGate(TripwireGate(scope), coding_agent_available=False)
    d = g.decide(triage(Route.CODING_AGENT, BlastRadius.MULTI_FILE), [])
    assert d.final_route is Route.HUMAN
    assert d.overridden


def test_human_route_passes_through(gate):
    g, _ = gate
    d = g.decide(triage(Route.HUMAN, BlastRadius.UNKNOWN), [])
    assert d.final_route is Route.HUMAN
    assert not d.overridden


def test_report_only_passes_through(gate):
    g, _ = gate
    d = g.decide(triage(Route.REPORT_ONLY, BlastRadius.RESTART), [])
    assert d.final_route is Route.REPORT_ONLY
    assert not d.overridden
