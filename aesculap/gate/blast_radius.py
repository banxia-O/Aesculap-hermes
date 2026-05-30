"""Blast-radius code gate (PRD §6.2) — the safety keystone.

This is the deterministic arbiter that embodies PRD §1: *the LLM proposes, the
code decides.* It takes the LLM's TriageDecision plus the lowered Proposed
actions and returns an authoritative GateDecision whose `final_route` overrides
whatever the LLM asked for.

Adjudication order (most-restrictive wins, fail-closed):

1. **Tripwires (§8.1)** — any action hitting a hard tripwire ⇒ `human`,
   no matter what. This is checked first and is absolute.
2. **Blast-radius downgrade (§6.2)** — if the LLM said `self_fix` but the
   blast radius is `multi_file`/`infra`, or the change is irreversible, or the
   radius is `unknown` ⇒ self_fix is forbidden; escalate to `coding_agent`
   (or `human` if no coding agent is available).
3. Otherwise the LLM's route stands.

`confidence` is never consulted (PRD §5.2).
"""

from __future__ import annotations

from aesculap.gate.tripwires import TripwireGate
from aesculap.types import (
    ActionKind,
    BlastRadius,
    GateDecision,
    NeedsHumanReason,
    ProposedAction,
    Route,
    TriageDecision,
)

# Blast radii for which self_fix is categorically forbidden (PRD §6.2).
_FORBIDDEN_FOR_SELF_FIX = {
    BlastRadius.MULTI_FILE,
    BlastRadius.INFRA,
    BlastRadius.UNKNOWN,
}


class BlastRadiusGate:
    """Routes a triage decision deterministically, overriding the LLM."""

    def __init__(self, tripwire_gate: TripwireGate, coding_agent_available: bool):
        self.tripwires = tripwire_gate
        self.coding_agent_available = coding_agent_available

    def _escalation_target(self) -> Route:
        """Where a forbidden self_fix escalates to (§6.3, §6.4).

        Prefer coding_agent; if none is installed, the route degrades to human.
        """
        return (
            Route.CODING_AGENT if self.coding_agent_available else Route.HUMAN
        )

    def decide(
        self, triage: TriageDecision, actions: list[ProposedAction]
    ) -> GateDecision:
        reasons: list[str] = []
        proposed = triage.route

        # --- 1. Hard tripwires (§8.1): absolute, beats everything ---------
        hits = self.tripwires.scan(actions)
        if hits:
            for h in hits:
                reasons.append(f"action[{h.action_index}]: {h.reason}")
            return GateDecision(
                final_route=Route.HUMAN,
                overridden=(proposed is not Route.HUMAN),
                proposed_route=proposed,
                reasons=reasons or ["tripwire fired"],
                needs_human_reason=NeedsHumanReason.NEEDS_HUMAN_ACTION,
            )

        # --- 2. Blast-radius downgrade (§6.2) -----------------------------
        if proposed is Route.SELF_FIX:
            radius_forbidden = triage.blast_radius in _FORBIDDEN_FOR_SELF_FIX
            irreversible = not triage.reversible
            # An arbitrary shell command has, by construction, an unbounded and
            # unknowable blast radius — the gate cannot statically prove what it
            # touches. So self_fix may never run a RUN_COMMAND: file edits must
            # arrive as scope-checked, shell-free write_file actions, and any
            # command is escalated to the coding_agent (which runs inside a git
            # checkpoint) or a human (§6.2). RESTART_PROCESS stays — it's the one
            # bounded, reversible, idempotent operation self_fix is meant for.
            has_command = any(a.kind is ActionKind.RUN_COMMAND for a in actions)
            if has_command or radius_forbidden or irreversible:
                target = self._escalation_target()
                why = []
                if has_command:
                    why.append("self_fix may not run shell commands "
                               "(unbounded blast radius)")
                if radius_forbidden:
                    why.append(f"blast_radius={triage.blast_radius.value}")
                if irreversible:
                    why.append("reversible=false")
                reasons.append(
                    f"self_fix forbidden by §6.2 ({', '.join(why)}); "
                    f"escalated to {target.value}"
                )
                return GateDecision(
                    final_route=target,
                    overridden=True,
                    proposed_route=proposed,
                    reasons=reasons,
                    needs_human_reason=(
                        NeedsHumanReason.NONE
                        if target is Route.CODING_AGENT
                        else NeedsHumanReason.NEEDS_HUMAN_ACTION
                    ),
                )

        # --- 3. coding_agent requested but unavailable (§6.4) -------------
        if proposed is Route.CODING_AGENT and not self.coding_agent_available:
            reasons.append(
                "coding_agent route but no coding agent installed; "
                "degraded to human (§6.4)"
            )
            return GateDecision(
                final_route=Route.HUMAN,
                overridden=True,
                proposed_route=proposed,
                reasons=reasons,
                needs_human_reason=NeedsHumanReason.NEEDS_HUMAN_ACTION,
            )

        # --- otherwise the LLM's route stands -----------------------------
        reasons.append(f"route {proposed.value} accepted (no override)")
        return GateDecision(
            final_route=proposed,
            overridden=False,
            proposed_route=proposed,
            reasons=reasons,
            needs_human_reason=triage.needs_human_reason,
        )
