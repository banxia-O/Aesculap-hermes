"""Notification message tests (PRD §8.3): four-part, actionable, key-safe."""

from aesculap.notify.message import build_message, scrub_secrets
from aesculap.types import NeedsHumanReason


def test_four_parts_present():
    msg = build_message(
        fault_summary="API down", triggering_probe="api_last_success",
        evidence="401 unauthorized", diagnosis="key expired",
        attempts=["restart: failed"],
        needs_human_reason=NeedsHumanReason.MISSING_KEY,
        one_line_fix="edit .env",
    )
    text = msg.render()
    assert "1) WHERE it broke" in text
    assert "2) WHAT was tried" in text
    assert "3) WHAT YOU need to do" in text
    assert "4) FIX GUIDANCE" in text
    assert "api_last_success" in text


def test_missing_key_guidance_does_not_request_key():
    msg = build_message(
        fault_summary="auth fail", triggering_probe="p", evidence="",
        diagnosis="", attempts=[],
        needs_human_reason=NeedsHumanReason.MISSING_KEY,
    )
    text = msg.render().lower()
    assert "do not paste the key" in text
    assert ".env" in text


def test_scrub_openai_key():
    out = scrub_secrets("here is sk-abcdEFGH12345678 ok")
    assert "sk-abcd" not in out
    assert "REDACTED" in out


def test_scrub_assignment_form():
    out = scrub_secrets("API_KEY=supersecretvalue")
    assert "supersecretvalue" not in out


def test_evidence_with_secret_is_scrubbed():
    msg = build_message(
        fault_summary="boom", triggering_probe="p",
        evidence="failed with token=ghp_aaaaaaaaaaaaaaaaaaaaaa here",
        diagnosis="", attempts=[],
        needs_human_reason=NeedsHumanReason.NONE,
    )
    assert "ghp_aaaa" not in msg.render()


def test_no_attempts_states_routed_directly():
    msg = build_message(
        fault_summary="x", triggering_probe="p", evidence="", diagnosis="",
        attempts=[], needs_human_reason=NeedsHumanReason.AMBIGUOUS,
    )
    assert "No automatic fix was attempted" in msg.render()


def test_payment_guidance():
    msg = build_message(
        fault_summary="x", triggering_probe="p", evidence="", diagnosis="",
        attempts=[], needs_human_reason=NeedsHumanReason.NEEDS_PAYMENT,
    )
    assert "billing" in msg.render().lower()
