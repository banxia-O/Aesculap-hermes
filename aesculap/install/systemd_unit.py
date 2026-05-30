"""systemd unit generation + install (PRD §2).

The installer lets the user choose the service scope (confirmed decision):

- **user service**  -> ~/.config/systemd/user/aesculap.service, runs as the
  Hermes user, no root; needs `loginctl enable-linger` for boot-start.
- **system service** -> /etc/systemd/system/aesculap.service, needs root,
  starts at boot.

systemd manages the lifecycle so a crashed daemon is auto-restarted (PRD §2);
Aesculap never restarts itself (§9.3).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "resources" / "aesculap.service.tmpl"
)

VALID_SCOPES = ("user", "system")


@dataclass
class SystemdPlan:
    scope: str  # "user" | "system"
    unit_path: Path
    content: str
    enable_hint: str


def _exec_start(config_path: str, python: str = "") -> str:
    python = python or sys.executable
    return f"{python} -m aesculap start {config_path}"


def render_unit(config_path: str, scope: str, working_directory: str = "") -> str:
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {VALID_SCOPES}; got {scope!r}")
    template = _TEMPLATE.read_text()
    wanted_by = "default.target" if scope == "user" else "multi-user.target"
    return template.format(
        exec_start=_exec_start(config_path),
        working_directory=working_directory or os.path.expanduser("~"),
        wanted_by=wanted_by,
    )


def plan_install(
    config_path: str, scope: str, working_directory: str = ""
) -> SystemdPlan:
    """Compute where the unit goes and what to run — WITHOUT writing anything.

    The wizard shows this plan; writing system files is a privileged action the
    user performs explicitly (we print the commands rather than sudo silently).
    """
    content = render_unit(config_path, scope, working_directory)
    if scope == "user":
        unit_path = (
            Path(os.path.expanduser("~/.config/systemd/user")) / "aesculap.service"
        )
        enable_hint = (
            "systemctl --user daemon-reload && "
            "systemctl --user enable --now aesculap.service\n"
            "# boot-start without an active login session:\n"
            "loginctl enable-linger $USER"
        )
    else:
        unit_path = Path("/etc/systemd/system/aesculap.service")
        enable_hint = (
            "sudo systemctl daemon-reload && "
            "sudo systemctl enable --now aesculap.service"
        )
    return SystemdPlan(scope, unit_path, content, enable_hint)


def write_unit(plan: SystemdPlan) -> None:
    """Write the unit file (user scope only writes under the user's home).

    For system scope this will require the process to already have permission;
    we never escalate privileges ourselves.
    """
    plan.unit_path.parent.mkdir(parents=True, exist_ok=True)
    plan.unit_path.write_text(plan.content)
