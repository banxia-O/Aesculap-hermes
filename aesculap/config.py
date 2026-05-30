"""Configuration loading, defaults, and validation (PRD §10).

Config is YAML (chosen for comments + nested probe definitions). This module
holds the dataclass schema, sane defaults, and strict validation. Everything
that the PRD calls "configurable" lives here so that no behaviour is silently
hard-coded — with the deliberate exception of the §9.2 blacklist *floor* and
the §8.1 tripwires, which are hard-wired in the gate and cannot be relaxed by
config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a config file is missing required fields or has bad values."""


# --- Writable-scope tiers (PRD §9.1) --------------------------------------
# There is intentionally NO default tier. The installer forces an explicit
# choice; a config that omits `scope.tier` is an error, not tier A.
VALID_TIERS = ("A", "B", "C")

# --- Mode bits (PRD §10.2) ------------------------------------------------
VALID_MODES = ("fix", "observe")


@dataclass
class ProbeConfig:
    """A single Tier 0 probe definition (PRD §3). Probes never call an LLM."""

    name: str
    type: str  # builtin probe id, e.g. "process_alive", "log_error_count"
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class ScopeConfig:
    """Writable scope + blacklist (PRD §9)."""

    tier: str  # "A" | "B" | "C" — REQUIRED, no default
    project_root: str = ""  # tier A/B/C: the Hermes project tree
    hermes_config_dir: str = ""  # tier B/C: e.g. ~/.hermes
    # User-selected identity files to blacklist (decision #1: installer scans
    # the Hermes config dir, user ticks the ones that are persona/memory).
    identity_files: list[str] = field(default_factory=list)
    # Extra user-supplied blacklist globs, merged on top of the hard floor.
    extra_blacklist: list[str] = field(default_factory=list)


@dataclass
class DebounceConfig:
    """De-bounce / escalation thresholds (PRD §4)."""

    recheck_seconds: int = 60
    consecutive_threshold: int = 2


@dataclass
class TriageConfig:
    """Tier 1 triage model selection (PRD §5.1). Provider-agnostic."""

    provider: str = ""  # "openai" | "anthropic" | "openai_compatible"
    model: str = ""
    base_url: str = ""  # for openai_compatible (vLLM/Ollama/etc.)
    api_key_env: str = ""  # env var name that holds the key; never the key itself


@dataclass
class SelfFixConfig:
    """Tier 2 self-fix model (PRD §5.1 suggests a cheaper model here)."""

    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    retry_budget: int = 3  # PRD §7.2: at most 3 self_fix attempts per bug
    observe_window_seconds: int = 60  # PRD §7.1 step 4: observation window


@dataclass
class CodingAgentConfig:
    """External coding tool for the coding_agent route (PRD §6.1, §6.4)."""

    tool: str = ""  # "claude" | "codex" | "" (none -> route degrades to human)
    command_template: str = ""  # how to invoke it; empty -> auto-detect at runtime


@dataclass
class NotifyConfig:
    """Notification channel (PRD §8.3, §11). Provider-agnostic: a command
    template the user fills in (e.g. ``hermes gateway send --text {message}``).
    """

    command_template: str = ""
    cooldown_seconds: int = 3600  # de-dup / cooldown for repeat issues


@dataclass
class DetectorsConfig:
    """Trigger architecture knobs (PRD §2)."""

    log_paths: list[str] = field(default_factory=list)
    error_patterns: list[str] = field(
        default_factory=lambda: [r"Traceback", r"CRITICAL", r"\bERROR\b"]
    )
    liveness_interval_seconds: int = 120  # 1-5 min range per PRD §2
    full_checkup_interval_seconds: int = 86400  # 24h default per PRD §2


@dataclass
class Config:
    """Top-level Aesculap config."""

    scope: ScopeConfig
    mode: str = "fix"  # PRD §10.2 default fix
    enabled: bool = True  # PRD §10.1 master switch
    aesculap_home: str = ""  # Aesculap's own dir (hard-blacklisted, §9.3)
    audit_log_path: str = ""
    state_dir: str = ""  # lockfile, open-issues state, backups
    probes: list[ProbeConfig] = field(default_factory=list)
    detectors: DetectorsConfig = field(default_factory=DetectorsConfig)
    debounce: DebounceConfig = field(default_factory=DebounceConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    selffix: SelfFixConfig = field(default_factory=SelfFixConfig)
    coding_agent: CodingAgentConfig = field(default_factory=CodingAgentConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def validate(self) -> None:
        """Fail loudly on anything that would make safety decisions unsound."""
        if self.scope.tier not in VALID_TIERS:
            raise ConfigError(
                f"scope.tier must be one of {VALID_TIERS} (explicit choice "
                f"required, PRD §9.1); got {self.scope.tier!r}"
            )
        if self.mode not in VALID_MODES:
            raise ConfigError(f"mode must be one of {VALID_MODES}; got {self.mode!r}")
        if self.scope.tier in ("A", "B") and not self.scope.project_root:
            raise ConfigError("scope.project_root is required for tier A/B")
        if self.scope.tier == "B" and not self.scope.hermes_config_dir:
            raise ConfigError("scope.hermes_config_dir is required for tier B")
        if self.debounce.consecutive_threshold < 1:
            raise ConfigError("debounce.consecutive_threshold must be >= 1")
        if self.selffix.retry_budget < 1:
            raise ConfigError("selffix.retry_budget must be >= 1")


def _build_nested(cls, data: dict[str, Any]):
    """Instantiate a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    try:
        return cls(**{k: v for k, v in data.items() if k in known})
    except TypeError as e:
        # Missing required field (e.g. scope.tier) — surface as a clean error.
        raise ConfigError(f"{cls.__name__}: {e}") from e


def load_config(path: str | os.PathLike[str]) -> Config:
    """Load, parse, and validate a YAML config file."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("top-level config must be a mapping")

    if "scope" not in raw or not isinstance(raw["scope"], dict):
        raise ConfigError("config must define a `scope` block (PRD §9.1)")

    scope = _build_nested(ScopeConfig, raw["scope"])
    probes = [_build_nested(ProbeConfig, p) for p in raw.get("probes", [])]

    nested_specs = {
        "detectors": DetectorsConfig,
        "debounce": DebounceConfig,
        "triage": TriageConfig,
        "selffix": SelfFixConfig,
        "coding_agent": CodingAgentConfig,
        "notify": NotifyConfig,
    }
    nested = {
        key: _build_nested(spec, raw[key])
        for key, spec in nested_specs.items()
        if isinstance(raw.get(key), dict)
    }

    scalar_keys = {
        "mode", "enabled", "aesculap_home", "audit_log_path", "state_dir",
    }
    scalars = {k: raw[k] for k in scalar_keys if k in raw}

    cfg = Config(scope=scope, probes=probes, **nested, **scalars)
    cfg.validate()
    return cfg
