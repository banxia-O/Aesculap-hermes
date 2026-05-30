"""Backup & rollback tests (PRD §7.1)."""

import subprocess

import pytest

from aesculap.remediate.backup import (
    BackupError,
    FileBackupManager,
    GitBackupManager,
)


# --- file backup ----------------------------------------------------------

def test_backup_and_restore_existing(tmp_path):
    f = tmp_path / "conf.json"
    f.write_text("original")
    mgr = FileBackupManager(str(tmp_path / "backups"))
    b = mgr.backup(str(f))
    f.write_text("modified")
    mgr.restore(b)
    assert f.read_text() == "original"


def test_restore_removes_created_file(tmp_path):
    """A fix that creates a new file: rollback deletes it (prior state absent)."""
    f = tmp_path / "new.txt"
    mgr = FileBackupManager(str(tmp_path / "backups"))
    b = mgr.backup(str(f))      # didn't exist
    f.write_text("created by fix")
    mgr.restore(b)
    assert not f.exists()


def test_backup_preserves_when_same_name(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("one")
    mgr = FileBackupManager(str(tmp_path / "backups"))
    b1 = mgr.backup(str(a))
    b2 = mgr.backup(str(a))
    assert b1.backup_path != b2.backup_path  # no clobber


# --- git backup -----------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("v1\n")
    git("add", "-A")
    git("commit", "-m", "init")
    return repo


def test_checkpoint_and_rollback(git_repo):
    mgr = GitBackupManager(str(git_repo))
    cp = mgr.checkpoint()
    (git_repo / "app.py").write_text("v2-broken\n")
    (git_repo / "new.py").write_text("junk\n")
    mgr.rollback(cp)
    assert (git_repo / "app.py").read_text() == "v1\n"
    assert not (git_repo / "new.py").exists()  # untracked cleaned


def test_commit_all_returns_sha(git_repo):
    mgr = GitBackupManager(str(git_repo))
    (git_repo / "app.py").write_text("v2\n")
    sha = mgr.commit_all("fix")
    assert len(sha) == 40


def test_is_git_repo(tmp_path, git_repo):
    assert GitBackupManager(str(git_repo)).is_git_repo()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not GitBackupManager(str(plain)).is_git_repo()


def test_checkpoint_non_repo_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(BackupError):
        GitBackupManager(str(plain)).checkpoint()
