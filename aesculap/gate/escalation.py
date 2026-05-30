"""Escalation ladder (PRD §6.3) — only up, never down.

```
self_fix (at most 3 attempts, §7.2)
  → still failing → coding_agent
    → coding_agent fails / not installed → human
```

This is a tiny deterministic state machine. It tracks, per bug, where on the
ladder we currently are, and refuses to ever step back down. The retry budget
for self_fix (§7.2) is enforced here too, since "ran out of self_fix attempts"
is exactly the trigger for the first rung of escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aesculap.types import Route

# The rungs, in ascending order. report_only is terminal and off-ladder.
_LADDER = [Route.SELF_FIX, Route.CODING_AGENT, Route.HUMAN]
_RANK = {route: i for i, route in enumerate(_LADDER)}


@dataclass
class EscalationState:
    """Per-bug escalation tracking (keyed by a stable issue fingerprint)."""

    current: Route = Route.SELF_FIX
    self_fix_attempts: int = 0
    history: list[str] = field(default_factory=list)


class EscalationLadder:
    """Enforces only-up movement and the self_fix retry budget."""

    def __init__(self, retry_budget: int = 3, coding_agent_available: bool = True):
        self.retry_budget = retry_budget
        self.coding_agent_available = coding_agent_available

    def clamp_up(self, state: EscalationState, target: Route) -> Route:
        """Return the higher of the current rung and `target` (never lower).

        Routes that aren't on the ladder (report_only) pass through unchanged —
        they represent "nothing to escalate".
        """
        if target not in _RANK:
            return target
        if state.current not in _RANK:
            state.current = target
            return target
        if _RANK[target] > _RANK[state.current]:
            state.current = target
        return state.current

    def next_after_self_fix_failure(self, state: EscalationState) -> Route:
        """Record a failed self_fix attempt and decide whether to escalate.

        Returns the route to use for the *next* action: still self_fix while
        budget remains, otherwise the next rung up (§6.3, §7.2).
        """
        state.self_fix_attempts += 1
        state.history.append(f"self_fix attempt {state.self_fix_attempts} failed")
        if state.self_fix_attempts < self.retry_budget:
            return self.clamp_up(state, Route.SELF_FIX)
        # Budget exhausted → escalate.
        target = Route.CODING_AGENT if self.coding_agent_available else Route.HUMAN
        state.history.append(
            f"self_fix budget ({self.retry_budget}) exhausted; "
            f"escalating to {target.value}"
        )
        return self.clamp_up(state, target)

    def next_after_coding_agent_failure(self, state: EscalationState) -> Route:
        """A coding_agent attempt failed → escalate to human (§6.3)."""
        state.history.append("coding_agent failed; escalating to human")
        return self.clamp_up(state, Route.HUMAN)
