"""Capability inventory (PRD §6.4).

Probes the environment for available tools, keys, and configured models so the
router knows what routes are actually executable. If a fix would need a
coding_agent but none is installed, that route auto-degrades to human (§6.4) —
the BlastRadiusGate consumes `coding_agent_available` for exactly this.

Detection is deterministic and side-effect-free (just `which` + env lookups).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from aesculap.config import Config

# Known external coding tools, in preference order (PRD §6.1 examples).
_KNOWN_CODING_AGENTS = ("claude", "codex")


@dataclass
class Capabilities:
    coding_agents: list[str] = field(default_factory=list)
    triage_key_present: bool = False
    selffix_key_present: bool = False
    notify_configured: bool = False

    @property
    def coding_agent_available(self) -> bool:
        return bool(self.coding_agents)


def _tool_on_path(name: str) -> bool:
    return shutil.which(name) is not None


def detect_capabilities(config: Config) -> Capabilities:
    """Build the capability inventory from the environment + config."""
    caps = Capabilities()

    # Coding agents: explicit config first, else auto-detect known tools.
    configured = config.coding_agent.tool.strip()
    if configured:
        if _tool_on_path(configured):
            caps.coding_agents.append(configured)
    else:
        caps.coding_agents = [t for t in _KNOWN_CODING_AGENTS if _tool_on_path(t)]

    # Keys (presence only; never read the value — PRD §8.3 key safety).
    if config.triage.api_key_env:
        caps.triage_key_present = bool(os.environ.get(config.triage.api_key_env))
    if config.selffix.api_key_env:
        caps.selffix_key_present = bool(os.environ.get(config.selffix.api_key_env))

    caps.notify_configured = bool(config.notify.command_template.strip())
    return caps
