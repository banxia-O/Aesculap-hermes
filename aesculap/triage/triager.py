"""Triager (PRD §5): call the triage LLM, parse, degrade to human on any fault.

Flow:
1. Build the prompt from the confirmed fault + current probe results.
2. Call the triage LLM (provider-agnostic adapter).
3. Parse + strictly validate the JSON (decision #3: bad input -> human).
4. Return a TriageDecision.

The LLM call itself is wrapped: a provider error (network, auth, missing SDK)
is also a degrade-to-human (decision #3 spirit — don't guess, no retry). The
returned decision still has to pass the §6.2 code gate afterward; the triager
never decides whether anything actually runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from aesculap.events import DetectionEvent
from aesculap.llm.base import LLMError, LLMProvider
from aesculap.probes.base import ProbeResult
from aesculap.triage.prompt import SYSTEM_PROMPT, build_user_prompt
from aesculap.triage.schema import ParseOutcome, _human_fallback, parse_triage
from aesculap.types import TriageDecision


@dataclass
class TriageResult:
    decision: TriageDecision
    degraded: bool
    reason: str
    raw_text: str = ""


class Triager:
    def __init__(self, provider: LLMProvider, max_tokens: int = 1024):
        self.provider = provider
        self.max_tokens = max_tokens

    def triage(
        self, event: DetectionEvent, probe_results: list[ProbeResult] | None = None
    ) -> TriageResult:
        probe_results = probe_results or []
        user = build_user_prompt(event, probe_results)
        try:
            resp = self.provider.complete(
                SYSTEM_PROMPT, user, max_tokens=self.max_tokens
            )
        except LLMError as e:
            outcome: ParseOutcome = _human_fallback(f"triage LLM call failed: {e}")
            return TriageResult(outcome.decision, True, outcome.reason, "")
        outcome = parse_triage(resp.text)
        return TriageResult(
            outcome.decision, outcome.degraded, outcome.reason, resp.text
        )
