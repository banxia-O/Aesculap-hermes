"""Install-time detection tests (PRD §5.1, §7.1, decision #1)."""

import subprocess

from aesculap.install.detect import (
    detect_providers,
    is_git_repo,
    scan_identity_candidates,
)


def test_detect_providers_from_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    providers = detect_providers()
    names = [p.name for p in providers]
    assert "anthropic" in names
    assert "openai" not in names
    assert all(p.suggested for p in providers)


def test_scan_identity_candidates(tmp_path):
    (tmp_path / "SOUL.md").write_text("persona")
    (tmp_path / "MEMORY.md").write_text("memory")
    (tmp_path / "config.yaml").write_text("x: 1")
    found = scan_identity_candidates(str(tmp_path))
    names = {f.rsplit("/", 1)[-1] for f in found}
    assert "SOUL.md" in names
    assert "MEMORY.md" in names
    assert "config.yaml" not in names


def test_scan_identity_nested(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "persona.md").write_text("x")
    found = scan_identity_candidates(str(tmp_path))
    assert any("persona.md" in f for f in found)


def test_scan_missing_dir_empty():
    assert scan_identity_candidates("/no/such/dir/zzz") == []


def test_is_git_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    assert not is_git_repo(str(repo))
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    assert is_git_repo(str(repo))
