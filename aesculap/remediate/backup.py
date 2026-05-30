"""Backup & rollback (PRD §7.1) — mandatory, not optional.

Before any fix touches the filesystem, the original must be recoverable:

- **config / plain files**: copy the original into a backup area before editing;
  on verification failure, restore it byte-for-byte.
- **code changes**: operate inside a git repo; a fix is a commit, and a failed
  fix is reverted with `git reset --hard` back to the pre-fix HEAD.

PRD §7.1 precondition: if the Hermes code is not in a git repo, install runs
`git init` first so coding_agent changes always land as commits. This module
provides both mechanisms; the executor picks per route (self_fix edits config →
file backup; coding_agent edits code → git).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class BackupError(RuntimeError):
    pass


# --- file backup/restore --------------------------------------------------

@dataclass
class FileBackup:
    """A snapshot of one file (or its prior non-existence)."""

    original_path: Path
    backup_path: Path | None  # None == file did not exist before
    existed: bool


class FileBackupManager:
    """Backs up individual files into a timestamped backup dir and restores."""

    def __init__(self, backup_root: str):
        self.backup_root = Path(backup_root)

    def backup(self, path: str) -> FileBackup:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            # Record that it didn't exist, so rollback deletes any file the fix
            # created (restoring the prior state of "absent").
            return FileBackup(original_path=p, backup_path=None, existed=False)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest_dir = self.backup_root / stamp
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (p.name + ".bak")
        # Avoid clobbering if multiple files share a name in one batch.
        i = 0
        while dest.exists():
            i += 1
            dest = dest_dir / f"{p.name}.{i}.bak"
        shutil.copy2(p, dest)
        return FileBackup(original_path=p, backup_path=dest, existed=True)

    def restore(self, backup: FileBackup) -> None:
        if not backup.existed:
            # File was created by the fix; restoring "absent" means removing it.
            if backup.original_path.exists():
                backup.original_path.unlink()
            return
        if backup.backup_path is None or not backup.backup_path.exists():
            raise BackupError(f"backup missing for {backup.original_path}")
        shutil.copy2(backup.backup_path, backup.original_path)


# --- git backup/rollback --------------------------------------------------

@dataclass
class GitCheckpoint:
    repo: Path
    head_sha: str
    dirty: bool  # whether there were uncommitted changes at checkpoint time


class GitBackupManager:
    """Checkpoints a git repo HEAD and rolls back to it on failure."""

    def __init__(self, repo: str):
        self.repo = Path(repo).expanduser().resolve()

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True,
        )
        if check and proc.returncode != 0:
            raise BackupError(
                f"git {' '.join(args)} failed: {proc.stderr.strip()}"
            )
        return proc

    def is_git_repo(self) -> bool:
        proc = self._git("rev-parse", "--is-inside-work-tree", check=False)
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def checkpoint(self) -> GitCheckpoint:
        if not self.is_git_repo():
            raise BackupError(f"{self.repo} is not a git repo (PRD §7.1 precondition)")
        head = self._git("rev-parse", "HEAD").stdout.strip()
        status = self._git("status", "--porcelain").stdout.strip()
        return GitCheckpoint(repo=self.repo, head_sha=head, dirty=bool(status))

    def commit_all(self, message: str) -> str:
        """Stage everything and commit; returns the new commit sha.

        Signing is explicitly disabled: these are automated checkpoint commits
        and must not depend on (or fail due to) a GPG/signing setup.
        """
        self._git("add", "-A")
        # Allow empty so a no-op fix still produces a checkpoint commit.
        self._git("-c", "commit.gpgsign=false", "commit", "-m", message,
                  "--allow-empty")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def rollback(self, checkpoint: GitCheckpoint) -> None:
        """Hard-reset back to the checkpoint HEAD and drop working changes."""
        self._git("reset", "--hard", checkpoint.head_sha)
        # Remove untracked files the fix may have created.
        self._git("clean", "-fd")
