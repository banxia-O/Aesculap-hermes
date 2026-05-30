"""Triage prompt construction (PRD §5.2, §8.2).

Builds the system + user prompt for the triage model. The system prompt:

- pins the role: diagnose + propose only; the *code* decides whether to act
  (PRD §1). The model is told its `self_fix` is a proposal, not authorization.
- mandates strict JSON output matching the §5.2 schema (no free text).
- teaches the §8.2 human-escalation signals (401/auth, CAPTCHA/OAuth/2FA,
  payment, ambiguous intent, repeated failure).

The user prompt carries the concrete evidence: the confirmed fault, the
triggering probe(s), and recent probe results.
"""

from __future__ import annotations

from aesculap.events import DetectionEvent
from aesculap.probes.base import ProbeResult

_SCHEMA_BLOCK = """{
  "diagnosis": "string, root-cause description",
  "blast_radius": "restart | single_file | multi_file | infra | unknown",
  "reversible": true,
  "confidence": 0.0,
  "route": "self_fix | coding_agent | human | report_only",
  "needs_human_reason": "null | missing_key | needs_payment | needs_human_action | ambiguous | repeated_failure",
  "actions": ["concrete action to take"]
}"""

SYSTEM_PROMPT = f"""You are the triage component of Aesculap, a self-healing \
plugin for an autonomous agent. Your ONLY job is to (1) diagnose the root cause \
and (2) propose a fix route. You DO NOT execute anything. Deterministic code \
downstream decides whether your proposal is allowed to run and may override \
your route entirely — so be honest, not optimistic.

Respond with EXACTLY ONE JSON object and nothing else. No prose, no markdown \
fences. Schema:

{_SCHEMA_BLOCK}

Field guidance:
- blast_radius is about SCOPE OF CHANGE, not difficulty:
  - restart: idempotent process/service restart, no file edits
  - single_file: one obvious edit to one file (typo, broken JSON), instantly verifiable
  - multi_file: needs reading the codebase / editing several files / iterating
  - infra: host/network/service-level (disk, OOM, systemd, DNS)
  - unknown: you cannot bound the change -> say unknown, do not guess
- route:
  - self_fix: known/one-step fix you can describe fully and verify immediately
  - coding_agent: needs code comprehension across files, tests, iteration
  - human: a human must act (see signals below)
  - report_only: nothing to fix (transient already recovered) or record-only
- reversible: false if the fix cannot be cleanly undone.
- confidence: 0..1, for the record only. It is NOT used to decide anything.
- actions: concrete, specific steps. For self_fix these must be executable and \
verifiable.

Emit route: human when you see any of:
- 401 / invalid api key / unauthorized  -> missing or expired key (missing_key)
- needs a verification link / CAPTCHA / OAuth / 2FA -> only a human can (needs_human_action)
- needs payment (renewal, top-up balance, buying resources) -> needs_payment
- ambiguous intent needing product/business judgment -> ambiguous
- the same bug has failed repeatedly -> repeated_failure

NEVER request secrets, API keys, or credentials in your output. If a key is \
needed, route to human with reason missing_key and describe WHERE the key goes, \
not its value."""


def build_user_prompt(
    event: DetectionEvent, probe_results: list[ProbeResult]
) -> str:
    """Assemble the evidence payload for one confirmed fault."""
    lines = [
        "A fault has been confirmed (it persisted through de-bounce).",
        "",
        f"Source detector: {event.source.value}",
        f"Summary: {event.summary}",
        f"Fingerprint: {event.fingerprint}",
    ]
    if event.evidence:
        lines += ["", "Triggering evidence:", "```", event.evidence.strip(), "```"]
    if probe_results:
        lines += ["", "Current Tier 0 probe results:"]
        for r in probe_results:
            ev = (r.evidence.splitlines()[0] if r.evidence else "")
            lines.append(f"- [{r.status.value}] {r.name}: {ev}")
    lines += ["", "Return the triage JSON object now."]
    return "\n".join(lines)
