"""De-dup + cooldown for notifications (PRD §11, §12).

Maintains an open-issues state file so an already-reported, still-pending issue
doesn't re-spam the user (PRD §12 "去重 + 冷却"). A fingerprint is suppressed
until either its cooldown elapses or it is explicitly resolved.

State is a small JSON file under the state dir (an Aesculap artifact, hence
inside the §9.3-blacklisted self area — the daemon can't "fix" it).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class NotificationDeduper:
    def __init__(self, state_path: str, cooldown_seconds: float = 3600):
        self.state_path = Path(state_path)
        self.cooldown_seconds = cooldown_seconds
        self._open: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if self.state_path.is_file():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._open, indent=2, sort_keys=True))

    def should_notify(self, fingerprint: str, now: float | None = None) -> bool:
        """True if this issue is not already within an active cooldown."""
        now = time.time() if now is None else now
        entry = self._open.get(fingerprint)
        if entry is None:
            return True
        return (now - entry["last_notified"]) >= self.cooldown_seconds

    def mark_notified(self, fingerprint: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        entry = self._open.get(fingerprint, {"first_notified": now, "count": 0})
        entry["last_notified"] = now
        entry["count"] = entry.get("count", 0) + 1
        self._open[fingerprint] = entry
        self._save()

    def resolve(self, fingerprint: str) -> None:
        """Clear an issue once its fault is fixed (so it can alert again later)."""
        if fingerprint in self._open:
            del self._open[fingerprint]
            self._save()

    def open_issues(self) -> list[str]:
        return sorted(self._open)
