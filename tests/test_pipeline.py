"""Pipeline tests (PRD §5/§6/§7.3): triage -> code gate, cascade protection.

The end-to-end safety property: no matter what the triage LLM proposes, the
authoritative route comes from the deterministic gate.
"""

import json

from aesculap.audit.log import AuditLog
from aesculap.config import ProbeConfig, ScopeConfig
from aesculap.events import DetectionEvent, EventSource
from aesculap.gate.blast_radius import BlastRadiusGate
from aesculap.gate.scope import ScopeGate
from aesculap.gate.tripwires import TripwireGate
from aesculap.llm.base import LLMProvider, LLMResponse
from aesculap.pipeline import Pipeline, lower_actions
from aesculap.probes.registry import ProbeSuite
from aesculap.triage.triager import Triager
from aesculap.types import ActionKind, Route


class FakeProvider(LLMProvider):
    def __init__(self, reply):
        super().__init__(model="fake")
        self._reply = reply

    def complete(self, system, user, *, max_tokens=1024):
        return LLMResponse(text=self._reply, model=self.model)


def triage_json(**over):
    base = {
        "diagnosis": "d", "blast_radius": "restart", "reversible": True,
        "confidence": 0.9, "route": "self_fix", "needs_human_reason": "null",
        "actions": ["systemctl restart hermes"],
    }
    base.update(over)
    return json.dumps(base)


def make_pipeline(tmp_path, reply, probes=None, coding_agent=True,
                  cascade_threshold=2):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    suite = ProbeSuite.from_config(probes or [])
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    gate = BlastRadiusGate(TripwireGate(scope), coding_agent_available=coding_agent)
    triager = Triager(FakeProvider(reply))
    audit = AuditLog(tmp_path / "audit.jsonl")
    return Pipeline(suite, triager, gate, audit,
                    cascade_fail_threshold=cascade_threshold), audit


def ev(related=None):
    return DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint="log:x",
                          summary="boom", related_probes=related or [])


def test_restart_self_fix_survives_gate(tmp_path):
    pipe, audit = make_pipeline(tmp_path, triage_json())
    out = pipe.process(ev())
    assert out.gate.final_route is Route.SELF_FIX
    assert not out.gate.overridden


def test_multi_file_self_fix_downgraded(tmp_path):
    pipe, _ = make_pipeline(
        tmp_path, triage_json(blast_radius="multi_file",
                              actions=["edit several files"]))
    out = pipe.process(ev())
    assert out.gate.final_route is Route.CODING_AGENT
    assert out.gate.overridden


def test_adversarial_rm_forced_to_human(tmp_path):
    """LLM says self_fix/restart but the action is rm -rf -> tripwire -> human."""
    pipe, _ = make_pipeline(
        tmp_path, triage_json(actions=["rm -rf /home/hermes"]))
    out = pipe.process(ev())
    assert out.gate.final_route is Route.HUMAN
    assert out.gate.overridden


def test_degraded_triage_routes_human(tmp_path):
    pipe, _ = make_pipeline(tmp_path, "not json")
    out = pipe.process(ev())
    assert out.triage_degraded
    assert out.gate.final_route is Route.HUMAN


def test_cascade_protection_to_human(tmp_path):
    """Two probes FAIL simultaneously -> system-level -> human, no triage."""
    probes = [
        ProbeConfig(name="d1", type="disk_free",
                    params={"warn_percent": 0, "fail_percent": 0}),
        ProbeConfig(name="d2", type="disk_free",
                    params={"warn_percent": 0, "fail_percent": 0}),
    ]
    # triage would say self_fix, but cascade fires first
    pipe, audit = make_pipeline(tmp_path, triage_json(), probes=probes)
    out = pipe.process(ev())
    assert out.gate.final_route is Route.HUMAN
    events = [r["event"] for r in audit.read_all()]
    assert "cascade_protection" in events
    assert "triage" not in events  # never spent a triage token


def test_coding_agent_unavailable_degrades(tmp_path):
    pipe, _ = make_pipeline(
        tmp_path, triage_json(route="coding_agent", blast_radius="multi_file"),
        coding_agent=False)
    out = pipe.process(ev())
    assert out.gate.final_route is Route.HUMAN


def test_audit_chain_recorded(tmp_path):
    pipe, audit = make_pipeline(tmp_path, triage_json())
    pipe.process(ev())
    events = [r["event"] for r in audit.read_all()]
    assert "triage" in events
    assert "gate" in events


# --- lower_actions ---------------------------------------------------------

def test_lower_restart_action():
    actions = lower_actions(["restart the hermes service"])
    assert actions[0].kind is ActionKind.RESTART_PROCESS


def test_lower_command_action():
    actions = lower_actions(["echo fix > /tmp/x"])
    assert actions[0].kind is ActionKind.RUN_COMMAND


def test_lower_skips_empty():
    assert lower_actions(["", "  "]) == []
