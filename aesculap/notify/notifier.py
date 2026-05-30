"""Notifier (PRD §8.3, §11) — send actionable human alerts via the gateway.

Provider-agnostic: notifications go out through a configured shell command
template (e.g. ``hermes gateway send --text {message}``), reusing Hermes'
existing messaging gateway. {message} is substituted with the rendered body.

Policy (PRD §11):
- only push on a real fix action or a human escalation; healthy silent runs
  never notify (the caller decides *when* to call us — we never spam).
- de-dup + cooldown so a pending issue isn't re-sent (delegated to the deduper).

Gateway-failure fallback (PRD §11): if sending fails, we record it for audit and
return False. Reviving the gateway itself is Hermes' own capability (Aesculap
reuses it); a total-host outage is beyond Aesculap's scope and falls back to
Hermes' existing mechanisms + ops.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from aesculap.notify.dedup import NotificationDeduper
from aesculap.notify.message import NotificationMessage


@dataclass
class SendResult:
    sent: bool
    suppressed: bool
    reason: str = ""


class Notifier:
    def __init__(
        self,
        command_template: str,
        deduper: NotificationDeduper | None = None,
        timeout_seconds: float = 30,
        runner=None,
    ):
        self.command_template = command_template
        self.deduper = deduper
        self.timeout_seconds = timeout_seconds
        self._runner = runner or self._run_subprocess

    def _run_subprocess(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=self.timeout_seconds,
        )

    def _build_command(self, message: str) -> str:
        if not self.command_template:
            raise ValueError("no notify command_template configured")
        if "{message}" not in self.command_template:
            return f"{self.command_template} {shlex.quote(message)}"
        return self.command_template.replace("{message}", shlex.quote(message))

    def notify(
        self, fingerprint: str, message: NotificationMessage
    ) -> SendResult:
        """Send a notification, honoring de-dup/cooldown and the channel."""
        if self.deduper is not None and not self.deduper.should_notify(fingerprint):
            return SendResult(False, True, "within cooldown (de-dup, §12)")
        rendered = message.render()
        try:
            proc = self._runner(self._build_command(rendered))
        except Exception as e:  # noqa: BLE001 - gateway send boundary
            return SendResult(False, False, f"gateway send failed: {e}")
        if proc.returncode != 0:
            return SendResult(
                False, False,
                f"gateway send exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:200]}",
            )
        if self.deduper is not None:
            self.deduper.mark_notified(fingerprint)
        return SendResult(True, False, "sent")
