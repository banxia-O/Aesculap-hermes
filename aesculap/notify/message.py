"""Notification message construction (PRD §8.3) — actionable, beginner-friendly.

A human-escalation notification MUST contain the four parts (PRD §8.3):

1. WHERE it broke   — fault description + triggering probe
2. WHAT was tried   — attempted fixes and their results
3. WHAT YOU must do  — concrete, path-level instructions: which key, which line
                       of which file, which link, which button
4. FIX GUIDANCE     — ideally a one-line "run X" for non-technical users

**Key-safety rule (mandatory, PRD §8.3):** when credentials/API keys are
involved, only tell the user WHERE the key goes (file/location) with a format
example — NEVER ask them to paste a key into chat or a log, and never echo a key
value. This module enforces that by construction: it builds guidance from the
`needs_human_reason`, and a small scrubber strips anything that looks like a key
from free-text fields before they enter the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aesculap.types import NeedsHumanReason

# Patterns that look like secrets in free text. We redact rather than transmit.
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),          # OpenAI-style
    re.compile(r"\b[A-Za-z0-9_\-]{0,4}ANTHROPIC[A-Za-z0-9_\-]*"),  # noisy guard
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),               # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),             # GitHub PAT
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),               # long hex tokens
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
]

_REDACTION = "[REDACTED]"


def scrub_secrets(text: str) -> str:
    """Redact anything that looks like a credential (key-safety, §8.3)."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_REDACTION, out)
    return out


# Per-reason actionable guidance. Each tells the user WHERE/ WHAT to do without
# ever requesting a secret value.
_REASON_GUIDANCE = {
    NeedsHumanReason.MISSING_KEY: (
        "An API key is missing or expired. Put a valid key in the configured "
        "credentials file (the env var named in config, e.g. `OPENAI_API_KEY` "
        "in your `.env`). Format example: `OPENAI_API_KEY=sk-...`. "
        "Do NOT paste the key here — set it in the file only, then restart Hermes."
    ),
    NeedsHumanReason.NEEDS_PAYMENT: (
        "This needs a payment action (renewal / balance top-up / resource "
        "purchase). Open your provider's billing page and complete it, then the "
        "next health check will clear automatically."
    ),
    NeedsHumanReason.NEEDS_HUMAN_ACTION: (
        "This needs a manual step only a person can do (verification link / "
        "CAPTCHA / OAuth / 2FA). Complete it in your browser, then Hermes will "
        "recover on the next check."
    ),
    NeedsHumanReason.AMBIGUOUS: (
        "The fix is ambiguous and needs a product/business decision. Review the "
        "diagnosis below and decide how to proceed."
    ),
    NeedsHumanReason.REPEATED_FAILURE: (
        "The same problem has failed to auto-fix repeatedly. It likely needs a "
        "deeper look than safe auto-repair allows."
    ),
    NeedsHumanReason.NONE: (
        "Automatic repair was not possible within safe boundaries. Review the "
        "details below."
    ),
}


@dataclass
class NotificationMessage:
    title: str
    body: str

    def render(self) -> str:
        return f"{self.title}\n\n{self.body}"


def build_message(
    *,
    fault_summary: str,
    triggering_probe: str,
    evidence: str,
    diagnosis: str,
    attempts: list[str],
    needs_human_reason: NeedsHumanReason,
    one_line_fix: str = "",
) -> NotificationMessage:
    """Assemble the four-part actionable notification (PRD §8.3)."""
    title = f"⚕ Aesculap needs you: {scrub_secrets(fault_summary)}"

    where = [
        "1) WHERE it broke",
        f"   • {scrub_secrets(fault_summary)}",
    ]
    if triggering_probe:
        where.append(f"   • Triggering probe: {triggering_probe}")
    if evidence:
        where.append(f"   • Evidence: {scrub_secrets(evidence.strip()[:300])}")
    if diagnosis:
        where.append(f"   • Diagnosis: {scrub_secrets(diagnosis)}")

    tried = ["2) WHAT was tried"]
    if attempts:
        tried += [f"   • {scrub_secrets(a)}" for a in attempts]
    else:
        tried.append("   • No automatic fix was attempted (routed to you directly).")

    todo = [
        "3) WHAT YOU need to do",
        "   " + _REASON_GUIDANCE.get(
            needs_human_reason, _REASON_GUIDANCE[NeedsHumanReason.NONE]
        ),
    ]

    guidance = ["4) FIX GUIDANCE"]
    if one_line_fix:
        guidance.append(f"   $ {scrub_secrets(one_line_fix)}")
    else:
        guidance.append("   Follow step 3; no one-line command applies here.")

    body = "\n".join(where + [""] + tried + [""] + todo + [""] + guidance)
    return NotificationMessage(title=title, body=body)
