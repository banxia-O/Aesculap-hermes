"""systemd unit generation tests (PRD §2)."""

import pytest

from aesculap.install.systemd_unit import plan_install, render_unit, write_unit


def test_user_unit_renders():
    unit = render_unit("/etc/aesculap/config.yaml", "user")
    assert "ExecStart=" in unit
    assert "-m aesculap start /etc/aesculap/config.yaml" in unit
    assert "WantedBy=default.target" in unit
    assert "Restart=always" in unit


def test_system_unit_wantedby():
    unit = render_unit("/c.yaml", "system")
    assert "WantedBy=multi-user.target" in unit


def test_invalid_scope_rejected():
    with pytest.raises(ValueError):
        render_unit("/c.yaml", "bogus")


def test_user_plan_paths():
    plan = plan_install("/c.yaml", "user")
    assert plan.unit_path.name == "aesculap.service"
    assert ".config/systemd/user" in str(plan.unit_path)
    assert "--user" in plan.enable_hint
    assert "enable-linger" in plan.enable_hint


def test_system_plan_paths():
    plan = plan_install("/c.yaml", "system")
    assert str(plan.unit_path) == "/etc/systemd/system/aesculap.service"
    assert "sudo systemctl" in plan.enable_hint


def test_write_unit_user_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    plan = plan_install("/c.yaml", "user")
    write_unit(plan)
    assert plan.unit_path.is_file()
    assert "ExecStart=" in plan.unit_path.read_text()
