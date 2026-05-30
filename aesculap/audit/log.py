"""Append-only audit log (PRD §13).

Records the whole chain: detection event → triage decision → gate verdict →
executed action → result. Each record is one JSON line (JSONL) so the log is
both human-greppable and machine-parseable, and append-only by construction
(opened in ``"a"`` mode, one fsync-friendly line per event).

The audit log is itself an Aesculap artifact and therefore lives under the
Aesculap home / state dir, which is hard-blacklisted (§9.3) — the daemon can
never "fix" its own audit trail.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of dataclasses / enums to JSON-safe values."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class AuditLog:
    """Append-only JSONL audit sink."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **fields: Any) -> dict[str, Any]:
        """Append one audit record. Returns the record (handy for tests)."""
        record = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "event": event_type,
            **{k: _jsonable(v) for k, v in fields.items()},
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def read_all(self) -> list[dict[str, Any]]:
        """Read back every record (for `aesculap status` / audits)."""
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
