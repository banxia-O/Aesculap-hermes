"""Capability inventory tests (PRD §6.4)."""

import os

from aesculap.config import (
    CodingAgentConfig,
    Config,
    NotifyConfig,
    ScopeConfig,
    TriageConfig,
)
from aesculap.install.capabilities import detect_capabilities


def base_config(**over):
    cfg = Config(scope=ScopeConfig(tier="A", project_root="/x"))
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_no_coding_agent_when_unconfigured_and_absent(monkeypatch):
    # Force `which` to find nothing.
    monkeypatch.setattr("shutil.which", lambda name: None)
    caps = detect_capabilities(base_config())
    assert caps.coding_agents == []
    assert not caps.coding_agent_available


def test_autodetect_known_agent(monkeypatch):
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    caps = detect_capabilities(base_config())
    assert "claude" in caps.coding_agents
    assert caps.coding_agent_available


def test_configured_agent_must_be_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    cfg = base_config(coding_agent=CodingAgentConfig(tool="codex"))
    caps = detect_capabilities(cfg)
    assert caps.coding_agents == []  # configured but not installed


def test_key_presence_detected(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("MY_TRIAGE_KEY", "sk-xxxx")
    cfg = base_config(triage=TriageConfig(provider="openai", model="m",
                                          api_key_env="MY_TRIAGE_KEY"))
    caps = detect_capabilities(cfg)
    assert caps.triage_key_present


def test_notify_configured(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    cfg = base_config(notify=NotifyConfig(command_template="send {message}"))
    caps = detect_capabilities(cfg)
    assert caps.notify_configured
