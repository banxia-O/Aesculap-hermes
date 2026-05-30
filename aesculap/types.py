"""Shared domain types for Aesculap.

These types are the vocabulary the deterministic safety gate (PRD §6.2, §8.1,
§9) speaks. They are intentionally defined here — not inside the triage layer —
so the gate has zero dependency on the LLM code path. The gate must be able to
adjudicate a decision even if the triage layer produced garbage.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Route(enum.Enum):
    """Where a fix is routed (PRD §6.1)."""

    SELF_FIX = "self_fix"
    CODING_AGENT = "coding_agent"
    HUMAN = "human"
    REPORT_ONLY = "report_only"

    @classmethod
    def from_str(cls, value: str) -> "Route":
        return cls(value)


class BlastRadius(enum.Enum):
    """How far a fix could blow up (PRD §5.2, §6.2).

    This — not difficulty, not LLM confidence — is what the code gate routes on.
    """

    RESTART = "restart"
    SINGLE_FILE = "single_file"
    MULTI_FILE = "multi_file"
    INFRA = "infra"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, value: str) -> "BlastRadius":
        return cls(value)


class NeedsHumanReason(enum.Enum):
    """Why a human is needed (PRD §5.2, §8.2)."""

    NONE = "null"
    MISSING_KEY = "missing_key"
    NEEDS_PAYMENT = "needs_payment"
    NEEDS_HUMAN_ACTION = "needs_human_action"
    AMBIGUOUS = "ambiguous"
    REPEATED_FAILURE = "repeated_failure"

    @classmethod
    def from_str(cls, value: str | None) -> "NeedsHumanReason":
        if value is None:
            return cls.NONE
        return cls(value)


class ActionKind(enum.Enum):
    """The concrete kind of operation a proposed fix wants to perform.

    The tripwire layer (§8.1) inspects these to decide whether an action is
    categorically forbidden, regardless of what the LLM routed it as.
    """

    WRITE_FILE = "write_file"  # create/modify a file at `path`
    DELETE_FILE = "delete_file"  # remove a file at `path`
    RUN_COMMAND = "run_command"  # run shell `command`
    RESTART_PROCESS = "restart_process"  # restart a managed process (idempotent)
    NONE = "none"  # no concrete side effect (e.g. report_only)


@dataclass
class ProposedAction:
    """A single concrete operation a fix intends to perform.

    The triage LLM emits free-text `actions`; the self-fix planner lowers those
    into structured ProposedActions. The gate only ever adjudicates structured
    actions — never free text — so an action that cannot be lowered into one of
    these kinds is, by construction, not self-fixable.
    """

    kind: ActionKind
    path: str | None = None
    command: str | None = None
    description: str = ""


@dataclass
class TriageDecision:
    """The validated output of the triage layer (PRD §5.2).

    This is what the LLM *proposes*. The gate may override `route` entirely.
    `confidence` is recorded for observability only and is NEVER a gate input
    (PRD §5.2: "confidence 仅供记录与观察，不作为闸门").
    """

    diagnosis: str
    blast_radius: BlastRadius
    reversible: bool
    route: Route
    needs_human_reason: NeedsHumanReason = NeedsHumanReason.NONE
    confidence: float = 0.0
    actions: list[str] = field(default_factory=list)


@dataclass
class GateDecision:
    """The deterministic verdict of the safety gate.

    `final_route` is authoritative and supersedes whatever the LLM proposed.
    `overridden` records whether the code changed the LLM's route, and `reasons`
    is the audit trail of which rules fired — both feed the append-only log.
    """

    final_route: Route
    overridden: bool
    proposed_route: Route
    reasons: list[str] = field(default_factory=list)
    needs_human_reason: NeedsHumanReason = NeedsHumanReason.NONE
