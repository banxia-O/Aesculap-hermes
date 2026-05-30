"""Scope + blacklist gate tests (PRD §9).

These assert the safety floor: blacklisted paths are refused inside ANY tier,
and tier boundaries are enforced. The blacklist must win over the tier.
"""

import os
import stat

import pytest

from aesculap.config import ScopeConfig
from aesculap.gate.scope import ScopeGate


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('hi')\n")
    return root


def make_gate(tmp_path, project, tier="A", **kw):
    cfg = ScopeConfig(
        tier=tier,
        project_root=str(project),
        hermes_config_dir=kw.get("hermes_config_dir", ""),
        identity_files=kw.get("identity_files", []),
        extra_blacklist=kw.get("extra_blacklist", []),
    )
    return ScopeGate(cfg, aesculap_home=kw.get("aesculap_home", ""))


def test_tier_a_allows_in_project(tmp_path, project):
    gate = make_gate(tmp_path, project)
    assert gate.check_write(str(project / "app.py")).allowed


def test_tier_a_denies_outside_project(tmp_path, project):
    gate = make_gate(tmp_path, project)
    v = gate.check_write(str(tmp_path / "elsewhere.txt"))
    assert not v.allowed
    assert "boundary" in v.reason


def test_path_traversal_escape_denied(tmp_path, project):
    gate = make_gate(tmp_path, project)
    # ../ escape must be normalized and rejected.
    v = gate.check_write(str(project / ".." / "secret.txt"))
    assert not v.allowed


def test_env_file_blacklisted_even_in_project(tmp_path, project):
    gate = make_gate(tmp_path, project)
    v = gate.check_write(str(project / ".env"))
    assert not v.allowed
    assert "credential" in v.reason


@pytest.mark.parametrize("name", [".env", ".env.production", "id_rsa",
                                  "server.key", "my_secret.txt", "API_TOKEN"])
def test_credential_patterns_blacklisted(tmp_path, project, name):
    gate = make_gate(tmp_path, project)
    assert not gate.check_write(str(project / name)).allowed


def test_identity_file_blacklisted(tmp_path, project):
    soul = project / "SOUL.md"
    soul.write_text("persona\n")
    gate = make_gate(tmp_path, project, identity_files=[str(soul)])
    v = gate.check_write(str(soul))
    assert not v.allowed
    assert "identity" in v.reason


def test_readonly_file_blacklisted(tmp_path, project):
    ro = project / "locked.conf"
    ro.write_text("x\n")
    ro.chmod(0o444)
    gate = make_gate(tmp_path, project)
    v = gate.check_write(str(ro))
    assert not v.allowed
    assert "read-only" in v.reason


def test_aesculap_self_dir_blacklisted(tmp_path, project):
    home = tmp_path / "aesculap_home"
    home.mkdir()
    gate = make_gate(tmp_path, project, tier="C", aesculap_home=str(home))
    v = gate.check_write(str(home / "config.py"))
    assert not v.allowed
    assert "self" in v.reason.lower()


def test_tier_c_allows_host_but_not_system(tmp_path, project):
    gate = make_gate(tmp_path, project, tier="C")
    # arbitrary user path allowed under C
    assert gate.check_write(str(tmp_path / "anywhere.txt")).allowed
    # system-sensitive still floored
    assert not gate.check_write("/etc/passwd").allowed
    assert not gate.check_write("/usr/bin/python").allowed


def test_tier_b_includes_config_dir(tmp_path, project):
    cfgdir = tmp_path / "hermes"
    cfgdir.mkdir()
    gate = make_gate(tmp_path, project, tier="B", hermes_config_dir=str(cfgdir))
    assert gate.check_write(str(cfgdir / "cli-config.yaml")).allowed
    assert gate.check_write(str(project / "app.py")).allowed
    assert not gate.check_write(str(tmp_path / "outside.txt")).allowed


def test_extra_blacklist_glob(tmp_path, project):
    gate = make_gate(tmp_path, project,
                     extra_blacklist=[str(project / "*.lock")])
    assert not gate.check_write(str(project / "db.lock")).allowed
    assert gate.check_write(str(project / "db.txt")).allowed
