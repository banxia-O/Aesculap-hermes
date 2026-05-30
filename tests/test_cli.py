"""CLI tests (PRD §10): config/probe/status/enable/disable/mode/install-systemd."""

import textwrap

import pytest

from aesculap.__main__ import main


def write_config(tmp_path, mode="fix", enabled=True):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        enabled: {str(enabled).lower()}
        mode: {mode}
        state_dir: {tmp_path}/state
        audit_log_path: {tmp_path}/state/audit.jsonl
        scope:
          tier: A
          project_root: {tmp_path}/proj
        probes:
          - name: disk
            type: disk_free
            params: {{warn_percent: 200, fail_percent: 300}}
    """))
    return str(p)


def test_config_ok(tmp_path, capsys):
    rc = main(["config", write_config(tmp_path)])
    assert rc == 0
    assert "config OK" in capsys.readouterr().out


def test_config_invalid(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("mode: fix\n")  # no scope
    rc = main(["config", str(bad)])
    assert rc == 1


def test_probe_ok(tmp_path, capsys):
    rc = main(["probe", write_config(tmp_path)])
    assert rc == 0
    assert "disk" in capsys.readouterr().out


def test_enable_disable_roundtrip(tmp_path, capsys):
    cfg = write_config(tmp_path, enabled=True)
    assert main(["disable", cfg]) == 0
    from aesculap.config import load_config
    assert load_config(cfg).enabled is False
    assert main(["enable", cfg]) == 0
    assert load_config(cfg).enabled is True


def test_mode_switch(tmp_path):
    cfg = write_config(tmp_path, mode="fix")
    assert main(["mode", "observe", cfg]) == 0
    from aesculap.config import load_config
    assert load_config(cfg).mode == "observe"


def test_status(tmp_path, capsys):
    cfg = write_config(tmp_path)
    rc = main(["status", cfg])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode=fix" in out
    assert "open issues" in out


def test_install_systemd_dry_run(tmp_path, capsys):
    cfg = write_config(tmp_path)
    rc = main(["install-systemd", cfg, "--scope", "user"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ExecStart=" in out
    assert "dry-run" in out


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
