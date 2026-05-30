"""Confirmed-fault pipeline: triage -> code gate (PRD §5, §6).

This is the seam between detection (Phase 2) and remediation (Phase 4). When a
fault survives de-bounce, the daemon hands it here. The pipeline:

1. re-runs the fault's related Tier 0 probes for fresh evidence (PRD §4 step 1)
2. checks cascade protection: if several probes FAIL at once, it's a
   system-level fault -> straight to human, no per-issue triage (PRD §7.3)
3. calls the triage LLM (PRD §5), strictly parsing its JSON (decision #3)
4. lowers the proposed free-text actions into structured ProposedActions
5. runs the §6.2/§8.1 code gate over the decision, producing the authoritative
   route the model can never override (PRD §1)

It records every step to the append-only audit log (PRD §13). It returns the
GateDecision; actually performing the fix is Phase 4 and is additionally gated
on `mode == fix` there (PRD §10.2). In `observe` mode the pipeline still runs
detection + triage + gate for the record, but the daemon must not act.
"""

from __future__ import annotations

from dataclasses import dataclass

from aesculap.audit.log import AuditLog
from aesculap.events import DetectionEvent
from aesculap.gate.blast_radius import BlastRadiusGate
from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.probes.registry import ProbeSuite
from aesculap.triage.triager import Triager
from aesculap.types import (
    ActionKind,
    BlastRadius,
    GateDecision,
    NeedsHumanReason,
    ProposedAction,
    Route,
    TriageDecision,
)


@dataclass
class PipelineOutcome:
    event: DetectionEvent
    triage: TriageDecision
    gate: GateDecision
    probe_results: list[ProbeResult]
    triage_degraded: bool


# Lowering of triage action items into structured ProposedActions for the gate.
# Structured objects (e.g. write_file with path+content) are passed through with
# their kind intact; free text is classified conservatively — a restart phrase
# becomes RESTART_PROCESS, anything else becomes a RUN_COMMAND with the raw text
# so the command-shaped tripwires inspect it. The gate fails closed, so
# mis-lowering errs toward escalation, never toward unsafe execution. (Note: a
# RUN_COMMAND in a self_fix proposal is escalated off self_fix by §6.2 — its
# blast radius is unbounded — so file edits must come as write_file objects.)
def lower_actions(actions: list) -> list[ProposedAction]:
    lowered: list[ProposedAction] = []
    for item in actions:
        # Structured action object (from the triage schema): already classified.
        if isinstance(item, dict):
            kind = item.get("kind")
            if kind == "write_file":
                lowered.append(ProposedAction(
                    kind=ActionKind.WRITE_FILE,
                    path=item.get("path"),
                    content=item.get("content", ""),
                    description=f"write {item.get('path')}",
                ))
            elif kind == "restart":
                lowered.append(ProposedAction(
                    kind=ActionKind.RESTART_PROCESS,
                    command=item.get("target") or None,
                    description=item.get("target") or "restart",
                ))
            elif kind == "run_command":
                cmd = item.get("command", "")
                lowered.append(ProposedAction(
                    kind=ActionKind.RUN_COMMAND, command=cmd, description=cmd))
            continue
        # Free-text action: heuristic lowering.
        t = str(item).strip()
        low = t.lower()
        if not t:
            continue
        if low.startswith(("restart", "systemctl restart", "service ")):
            lowered.append(
                ProposedAction(kind=ActionKind.RESTART_PROCESS, description=t)
            )
        else:
            # Default: treat as a command so command-shaped tripwires apply.
            lowered.append(
                ProposedAction(kind=ActionKind.RUN_COMMAND, command=t, description=t)
            )
    return lowered


class Pipeline:
    def __init__(
        self,
        suite: ProbeSuite,
        triager: Triager,
        gate: BlastRadiusGate,
        audit: AuditLog,
        cascade_fail_threshold: int = 2,
    ):
        self.suite = suite
        self.triager = triager
        self.gate = gate
        self.audit = audit
        self.cascade_fail_threshold = cascade_fail_threshold

    def _cascade_decision(self, results: list[ProbeResult]) -> GateDecision | None:
        """PRD §7.3: many probes FAIL at once -> system-level -> human."""
        fails = [r for r in results if r.status is ProbeStatus.FAIL]
        if len(fails) >= self.cascade_fail_threshold:
            names = ", ".join(r.name for r in fails)
            return GateDecision(
                final_route=Route.HUMAN,
                overridden=False,
                proposed_route=Route.HUMAN,
                reasons=[
                    f"cascade protection: {len(fails)} probes FAILED "
                    f"simultaneously ({names}); system-level fault (§7.3)"
                ],
                needs_human_reason=NeedsHumanReason.NEEDS_HUMAN_ACTION,
            )
        return None

    def process(self, event: DetectionEvent) -> PipelineOutcome:
        # 1. Fresh evidence: re-run the fault's related probes (or all if none).
        if event.related_probes:
            results = self.suite.run_subset(event.related_probes)
        else:
            results = self.suite.run_all()

        # 2. Cascade protection BEFORE spending a triage token (§7.3).
        cascade = self._cascade_decision(results)
        if cascade is not None:
            degraded_triage = TriageDecision(
                diagnosis="cascade: multiple simultaneous probe failures",
                blast_radius=BlastRadius.INFRA,
                reversible=False,
                route=Route.HUMAN,
                needs_human_reason=NeedsHumanReason.NEEDS_HUMAN_ACTION,
            )
            self.audit.record(
                "cascade_protection",
                fingerprint=event.fingerprint,
                failed_probes=[r.name for r in results if r.status is ProbeStatus.FAIL],
                gate=cascade,
            )
            return PipelineOutcome(event, degraded_triage, cascade, results, False)

        # 3. Triage (LLM proposes).
        tr = self.triager.triage(event, results)
        self.audit.record(
            "triage",
            fingerprint=event.fingerprint,
            decision=tr.decision,
            degraded=tr.degraded,
            reason=tr.reason,
        )

        # 4. Lower actions, 5. code gate decides (and may override the LLM).
        actions = lower_actions(tr.decision.actions)
        gate_decision = self.gate.decide(tr.decision, actions)
        self.audit.record(
            "gate",
            fingerprint=event.fingerprint,
            gate=gate_decision,
            proposed_route=tr.decision.route,
            final_route=gate_decision.final_route,
            overridden=gate_decision.overridden,
        )
        return PipelineOutcome(
            event, tr.decision, gate_decision, results, tr.degraded
        )
