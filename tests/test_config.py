"""Config loading + validation tests (PRD §10, §9.1)."""

import textwrap

import pytest

from aesculap.config import ConfigError, load_config


def write_cfg(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_minimal_valid_config(tmp_path):
    p = write_cfg(tmp_path, """
        scope:
          tier: A
          project_root: /home/hermes/project
    """)
    cfg = load_config(p)
    assert cfg.scope.tier == "A"
    assert cfg.mode == "fix"          # default
    assert cfg.enabled is True
    assert cfg.selffix.retry_budget == 3


def test_missing_scope_block_rejected(tmp_path):
    p = write_cfg(tmp_path, "mode: fix\n")
    with pytest.raises(ConfigError, match="scope"):
        load_config(p)


def test_missing_tier_rejected(tmp_path):
    """No default tier — explicit choice is mandatory (PRD §9.1)."""
    p = write_cfg(tmp_path, """
        scope:
          project_root: /x
    """)
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_tier_rejected(tmp_path):
    p = write_cfg(tmp_path, """
        scope:
          tier: Z
          project_root: /x
    """)
    with pytest.raises(ConfigError, match="tier"):
        load_config(p)


def test_invalid_mode_rejected(tmp_path):
    p = write_cfg(tmp_path, """
        mode: yolo
        scope:
          tier: A
          project_root: /x
    """)
    with pytest.raises(ConfigError, match="mode"):
        load_config(p)


def test_tier_b_requires_config_dir(tmp_path):
    p = write_cfg(tmp_path, """
        scope:
          tier: B
          project_root: /x
    """)
    with pytest.raises(ConfigError, match="hermes_config_dir"):
        load_config(p)


def test_probes_parsed(tmp_path):
    p = write_cfg(tmp_path, """
        scope:
          tier: A
          project_root: /x
        probes:
          - name: p1
            type: process_alive
            params:
              pattern: hermes
    """)
    cfg = load_config(p)
    assert len(cfg.probes) == 1
    assert cfg.probes[0].type == "process_alive"
    assert cfg.probes[0].params["pattern"] == "hermes"


def test_unknown_key_rejected(tmp_path):
    p = write_cfg(tmp_path, """
        scope:
          tier: A
          project_root: /x
          bogus_key: 1
    """)
    with pytest.raises(ConfigError, match="unknown"):
        load_config(p)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
