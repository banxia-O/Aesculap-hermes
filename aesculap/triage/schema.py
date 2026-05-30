"""Triage output schema + strict parsing (PRD §5.2, decision #3).

The triage LLM must return structured JSON (PRD §5.2, "禁止自由发挥"). This
module parses and *strictly* validates it into a TriageDecision.

**Decision #3 (fills a PRD gap):** if the JSON fails to parse, has illegal
field values, or is missing the `route` field, we DO NOT guess and DO NOT
retry — we degrade straight to `route: human`. The safe direction is always to
ask a person. This degradation is deterministic, lives outside the LLM, and is
surfaced with a reason for the audit log.

Note this is distinct from the §6.2 gate: here we produce a *valid* decision
(falling back to human on bad input); the gate then independently adjudicates
that decision. A malformed triage can never sneak `self_fix` through.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from aesculap.types import (
    BlastRadius,
    NeedsHumanReason,
    Route,
    TriageDecision,
)


@dataclass
class ParseOutcome:
    """Result of parsing triage output.

    `decision` is always present (degraded to human on any problem).
    `degraded` is True when the fallback fired; `reason` explains why (audit).
    """

    decision: TriageDecision
    degraded: bool
    reason: str = ""


def _human_fallback(reason: str, diagnosis: str = "") -> ParseOutcome:
    """Build the deterministic human-route fallback (decision #3)."""
    return ParseOutcome(
        decision=TriageDecision(
            diagnosis=diagnosis or f"triage degraded to human: {reason}",
            blast_radius=BlastRadius.UNKNOWN,
            reversible=False,
            route=Route.HUMAN,
            needs_human_reason=NeedsHumanReason.AMBIGUOUS,
            confidence=0.0,
            actions=[],
        ),
        degraded=True,
        reason=reason,
    )


def _extract_json(text: str) -> str | None:
    """Pull the first JSON object out of a model response.

    Models sometimes wrap JSON in prose or ```json fences. We try the whole
    string first, then a fenced block, then the first {...} span. Anything we
    can't isolate returns None -> human fallback.
    """
    text = text.strip()
    if not text:
        return None
    # Fenced ```json ... ``` block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    # First balanced-looking object span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def parse_triage(text: str) -> ParseOutcome:
    """Parse + strictly validate triage JSON; degrade to human on any fault."""
    blob = _extract_json(text)
    if blob is None:
        return _human_fallback("no JSON object found in triage output")
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError) as e:
        return _human_fallback(f"JSON parse error: {e}")
    if not isinstance(data, dict):
        return _human_fallback("triage JSON is not an object")

    # `route` is mandatory (decision #3): missing -> human.
    if "route" not in data:
        return _human_fallback("triage JSON missing required `route` field")
    try:
        route = Route.from_str(str(data["route"]))
    except ValueError:
        return _human_fallback(f"illegal route value: {data.get('route')!r}")

    # blast_radius: missing/illegal -> unknown is itself a safe value, but per
    # decision #3 an *illegal* value is bad input -> human. A missing one we
    # treat as unknown (which the §6.2 gate forbids self_fix on anyway).
    if "blast_radius" in data:
        try:
            blast = BlastRadius.from_str(str(data["blast_radius"]))
        except ValueError:
            return _human_fallback(
                f"illegal blast_radius value: {data.get('blast_radius')!r}"
            )
    else:
        blast = BlastRadius.UNKNOWN

    # reversible: must be a real bool if present; anything weird -> human.
    reversible = data.get("reversible", True)
    if not isinstance(reversible, bool):
        return _human_fallback(f"reversible must be boolean, got {reversible!r}")

    # needs_human_reason: tolerate null/missing; illegal string -> human.
    nhr_raw = data.get("needs_human_reason")
    try:
        nhr = NeedsHumanReason.from_str(
            None if nhr_raw in (None, "null") else str(nhr_raw)
        )
    except ValueError:
        return _human_fallback(f"illegal needs_human_reason: {nhr_raw!r}")

    # confidence: record-only (§5.2); coerce, default 0.0, never a gate.
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    actions = data.get("actions", [])
    if not isinstance(actions, list):
        return _human_fallback("actions must be a list")
    actions = [str(a) for a in actions]

    diagnosis = str(data.get("diagnosis", "")).strip()

    return ParseOutcome(
        decision=TriageDecision(
            diagnosis=diagnosis,
            blast_radius=blast,
            reversible=reversible,
            route=route,
            needs_human_reason=nhr,
            confidence=confidence,
            actions=actions,
        ),
        degraded=False,
    )
