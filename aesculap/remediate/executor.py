"""Remediation orchestrator (PRD §6.3, §7, §10.2).

Takes a gate-approved route and drives the fix to a terminal state, walking the
only-up escalation ladder (§6.3):

    self_fix (≤3 attempts) -> coding_agent -> human

Responsibilities:
- enforce `mode == fix` before ANY action (observe mode never acts, §10.2)
- pick the executor for the final route
- on self_fix exhaustion, escalate to coding_agent; on coding_agent failure /
  unavailability, escalate to human
- emit a human notification when the terminal route is human (the actual
  send is injected; Phase 5 provides the notifier)
- audit every step (§13)

What it does NOT do: decide whether an action is *allowed* (the §6.2/§8.1 gate
already did) or re-route around the gate. It only sequences approved work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from aesculap.audit.log import AuditLog
from aesculap.gate.escalation import EscalationLadder, EscalationState
from aesculap.pipeline import PipelineOutcome, lower_actions
from aesculap.remediate.coding_agent import CodingAgentExecutor
from aesculap.remediate.selffix import SelfFixExecutor
from aesculap.types import Route

# Injected hook: notify a human (Phase 5 notifier). Receives the outcome +
# reason so it can build the §8.3 actionable message.
NotifyFn = Callable[[PipelineOutcome, str], None]


def _action_summary(a) -> str:
    """Readable one-liner for an action item (string or structured object)."""
    if isinstance(a, dict):
        kind = a.get("kind", "action")
        target = a.get("path") or a.get("command") or a.get("target") or ""
        return f"{kind}: {target}".rstrip(": ")
    return str(a)


@dataclass
class RemediationResult:
    final_route: Route
    success: bool
    reason: str
    attempts: int = 0
    history: list[str] = field(default_factory=list)


class RemediationExecutor:
    def __init__(
        self,
        mode: str,
        audit: AuditLog,
        selffix: SelfFixExecutor | None = None,
        coding_agent: CodingAgentExecutor | None = None,
        ladder: EscalationLadder | None = None,
        notify_fn: NotifyFn | None = None,
    ):
        self.mode = mode
        self.audit = audit
        self.selffix = selffix
        self.coding_agent = coding_agent
        self.ladder = ladder or EscalationLadder()
        self.notify_fn = notify_fn

    def remediate(self, outcome: PipelineOutcome) -> RemediationResult:
        route = outcome.gate.final_route
        fp = outcome.event.fingerprint

        # observe mode: detect + triage + gate already happened; never act.
        if self.mode != "fix":
            self.audit.record("remediation_skipped", fingerprint=fp,
                              reason="observe mode (§10.2)", route=route)
            return RemediationResult(route, False, "observe mode: no action taken")

        # report_only: nothing to do.
        if route is Route.REPORT_ONLY:
            self.audit.record("report_only", fingerprint=fp)
            return RemediationResult(route, True, "report_only")

        # human: notify and stop.
        if route is Route.HUMAN:
            return self._escalate_human(outcome, "; ".join(outcome.gate.reasons))

        state = EscalationState()
        before = outcome.probe_results
        history: list[str] = []

        # --- self_fix loop (≤ retry budget, §7.2) -------------------------
        if route is Route.SELF_FIX:
            if self.selffix is None:
                return self._escalate_human(outcome, "no self_fix executor configured")
            actions = lower_actions(outcome.triage.actions)
            while True:
                attempt = self.selffix.run(actions, before, state)
                history.append(attempt.reason)
                self.audit.record(
                    "self_fix_attempt", fingerprint=fp,
                    attempt=attempt.attempts, success=attempt.success,
                    reason=attempt.reason,
                )
                if attempt.success:
                    return RemediationResult(
                        Route.SELF_FIX, True, attempt.reason,
                        attempt.attempts, history,
                    )
                if attempt.next_route is Route.SELF_FIX:
                    continue  # budget remains; try again
                route = attempt.next_route  # budget exhausted -> escalate
                break

        # --- coding_agent (§6.1) ------------------------------------------
        if route is Route.CODING_AGENT:
            if self.coding_agent is None or not self.coding_agent.available():
                return self._escalate_human(
                    outcome, "coding_agent unavailable (§6.4)", history)
            prompt = self._coding_prompt(outcome)
            ca = self.coding_agent.run(prompt, before)
            history.append(ca.reason)
            self.audit.record("coding_agent_attempt", fingerprint=fp,
                             success=ca.success, reason=ca.reason,
                             commit=ca.commit_sha)
            if ca.success:
                return RemediationResult(
                    Route.CODING_AGENT, True, ca.reason, history=history)
            route = Route.HUMAN  # coding_agent failed -> human (§6.3)

        # --- terminal human ------------------------------------------------
        return self._escalate_human(outcome, "; ".join(history) or "escalated",
                                    history)

    def _coding_prompt(self, outcome: PipelineOutcome) -> str:
        t = outcome.triage
        lines = [
            "Aesculap detected a fault and routed it to you (coding agent).",
            f"Diagnosis: {t.diagnosis}",
            f"Triggering fault: {outcome.event.summary}",
        ]
        if outcome.event.evidence:
            lines.append(f"Evidence:\n{outcome.event.evidence}")
        if t.actions:
            lines.append("Suggested actions:\n- "
                         + "\n- ".join(_action_summary(a) for a in t.actions))
        lines.append("Make the smallest change that fixes the root cause. "
                     "Do not touch identity files, credentials, or .env.")
        return "\n".join(lines)

    def _escalate_human(
        self, outcome: PipelineOutcome, reason: str,
        history: list[str] | None = None,
    ) -> RemediationResult:
        self.audit.record("escalate_human", fingerprint=outcome.event.fingerprint,
                         reason=reason)
        if self.notify_fn is not None:
            self.notify_fn(outcome, reason)
        return RemediationResult(Route.HUMAN, False, reason, history=history or [])
