"""Wizard config-assembly tests (PRD §9.1, decision #1).

Tests the pure `build_config_dict` / `write_config` logic; the interactive shell
is exercised lightly via input monkeypatching.
"""

import builtins

import pytest

from aesculap.config import ConfigError
from aesculap.install.wizard import (
    WizardAnswers,
    build_config_dict,
    write_config,
)


def base_answers(**over):
    a = WizardAnswers(tier="A", project_root="/home/h/proj")
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_tier_a_config():
    cfg = build_config_dict(base_answers())
    assert cfg["scope"]["tier"] == "A"
    assert cfg["mode"] == "fix"
    assert cfg["enabled"] is True


def test_tier_must_be_explicit():
    with pytest.raises(ValueError):
        build_config_dict(base_answers(tier="Z"))


def test_tier_b_requires_config_dir():
    with pytest.raises(ValueError):
        build_config_dict(base_answers(tier="B"))


def test_tier_b_with_config_dir():
    cfg = build_config_dict(base_answers(tier="B", hermes_config_dir="/home/h/.hermes"))
    assert cfg["scope"]["hermes_config_dir"] == "/home/h/.hermes"


def test_identity_files_written():
    cfg = build_config_dict(base_answers(identity_files=["/h/.hermes/SOUL.md"]))
    assert cfg["scope"]["identity_files"] == ["/h/.hermes/SOUL.md"]


def test_triage_block_written():
    cfg = build_config_dict(base_answers(
        triage_provider="anthropic", triage_model="m", triage_key_env="ANTHROPIC_API_KEY"))
    assert cfg["triage"]["provider"] == "anthropic"
    assert cfg["triage"]["api_key_env"] == "ANTHROPIC_API_KEY"


def test_write_config_validates(tmp_path):
    out = tmp_path / "config.yaml"
    cfg = write_config(base_answers(
        triage_provider="anthropic", triage_model="m",
        notify_command_template="send {message}",
        log_paths=["/var/log/h.log"]), str(out))
    assert out.is_file()
    assert cfg.scope.tier == "A"
    assert cfg.mode == "fix"


def test_invalid_systemd_scope_rejected():
    with pytest.raises(ValueError):
        build_config_dict(base_answers(systemd_scope="bogus"))


# --- interactive: forced tier + tier C double-confirm ---------------------

def _fake_inputs(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(it))


# --- git_init seeds a .gitignore that excludes bulky/ephemeral runtime dirs --

def test_ensure_gitignore_creates_block(tmp_path):
    from aesculap.install.wizard import ensure_gitignore

    ensure_gitignore(str(tmp_path))
    body = (tmp_path / ".gitignore").read_text()
    for excl in ("logs/", "sessions/", "cron/output/", "image_cache/"):
        assert excl in body


def test_ensure_gitignore_idempotent(tmp_path):
    from aesculap.install.wizard import ensure_gitignore

    ensure_gitignore(str(tmp_path))
    first = (tmp_path / ".gitignore").read_text()
    ensure_gitignore(str(tmp_path))
    assert (tmp_path / ".gitignore").read_text() == first  # no duplicate block


def test_ensure_gitignore_preserves_existing(tmp_path):
    from aesculap.install.wizard import ensure_gitignore

    gi = tmp_path / ".gitignore"
    gi.write_text("*.pyc\n")
    ensure_gitignore(str(tmp_path))
    body = gi.read_text()
    assert body.startswith("*.pyc\n")   # user content untouched
    assert "logs/" in body              # our block appended


def test_git_init_seeds_gitignore(tmp_path):
    from aesculap.install.wizard import git_init

    repo = tmp_path / "repo"
    repo.mkdir()
    assert git_init(str(repo))
    assert (repo / ".gitignore").exists()
    assert "image_cache/" in (repo / ".gitignore").read_text()


def test_run_wizard_tier_c_aborts_on_no(monkeypatch, tmp_path):
    from aesculap.install import wizard
    # project, hermes cfg, tier=C, "dedicated?" -> no  => abort with code 1
    _fake_inputs(monkeypatch, [
        str(tmp_path / "proj"),  # project root
        str(tmp_path / "hermes"),  # hermes cfg dir
        "C",                       # tier
        "n",                       # dedicated host? no
    ])
    rc = wizard.run_wizard(str(tmp_path / "out.yaml"))
    assert rc == 1
    assert not (tmp_path / "out.yaml").exists()
