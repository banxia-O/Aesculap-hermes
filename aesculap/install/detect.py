"""Install-time environment self-detection (PRD §5.1, §2, §7.1, decision #1).

Pure inspection helpers the wizard uses to propose sensible defaults without
hard-coding anything Hermes-specific into the engine:

- configured model providers, by which API-key env vars are present (§5.1)
- the Hermes config dir + candidate identity files to blacklist (decision #1)
- whether Hermes appears to log to a file (§2 precondition)
- whether the project is a git repo (§7.1 precondition)

All detection is read-only and side-effect-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Known provider -> the env var that, if set, indicates it's configured.
_PROVIDER_KEY_ENVS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Default Hermes config dir (from the Hermes repo); the wizard can override.
DEFAULT_HERMES_CONFIG_DIR = "~/.hermes"

# Filenames that are likely identity / persona / memory files (PRD §9.2). These
# are *candidates* the user ticks — never auto-blacklisted silently.
_IDENTITY_HINTS = (
    "soul.md", "soul", "persona.md", "persona", "memory.md", "memory",
    "user.md", "identity.md", "character.md", "personality.md",
)


@dataclass
class DetectedProvider:
    name: str
    key_env: str
    suggested: bool = False  # strong model recommended for triage (§5.1)


@dataclass
class EnvDetection:
    providers: list[DetectedProvider] = field(default_factory=list)
    coding_agents: list[str] = field(default_factory=list)
    identity_candidates: list[str] = field(default_factory=list)
    hermes_config_dir: str = ""
    is_git_repo: bool = False
    file_logging_hint: str = ""


def detect_providers() -> list[DetectedProvider]:
    """List providers that look configured (their key env var is set)."""
    found: list[DetectedProvider] = []
    for name, env in _PROVIDER_KEY_ENVS.items():
        if os.environ.get(env):
            # Anthropic/OpenAI both ship strong models; mark as suggested for the
            # safety-relevant triage step (§5.1 prefers a strong model there).
            found.append(DetectedProvider(name=name, key_env=env, suggested=True))
    return found


def detect_coding_agents() -> list[str]:
    return [t for t in ("claude", "codex") if shutil.which(t)]


def scan_identity_candidates(config_dir: str) -> list[str]:
    """Find likely identity files in the Hermes config dir (decision #1)."""
    base = Path(config_dir).expanduser()
    if not base.is_dir():
        return []
    candidates: list[str] = []
    for entry in sorted(base.rglob("*")):
        if entry.is_file() and entry.name.lower() in _IDENTITY_HINTS:
            candidates.append(str(entry))
    return candidates


def is_git_repo(path: str) -> bool:
    p = Path(path).expanduser()
    if not p.is_dir():
        return False
    proc = subprocess.run(
        ["git", "-C", str(p), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def detect_environment(
    hermes_config_dir: str = DEFAULT_HERMES_CONFIG_DIR, project_root: str = ""
) -> EnvDetection:
    cfg_dir = os.path.expanduser(hermes_config_dir)
    det = EnvDetection(
        providers=detect_providers(),
        coding_agents=detect_coding_agents(),
        identity_candidates=scan_identity_candidates(cfg_dir),
        hermes_config_dir=cfg_dir,
        is_git_repo=is_git_repo(project_root) if project_root else False,
    )
    # File-logging precondition (§2): Hermes defaults to stdout. We can't be
    # sure, so we surface a hint rather than asserting.
    logs_dir = Path(cfg_dir) / "logs"
    if logs_dir.is_dir() and any(logs_dir.iterdir()):
        det.file_logging_hint = f"found logs under {logs_dir}"
    else:
        det.file_logging_hint = (
            f"no logs found under {logs_dir}; ensure Hermes writes to a log "
            f"file (PRD §2 precondition) — suggested: {logs_dir}/hermes.log"
        )
    return det
