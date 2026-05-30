"""coding_agent executor tests (PRD §6.1, §7.1)."""

import subprocess

import pytest

from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.remediate.backup import GitBackupManager
from aesculap.remediate.coding_agent import CodingAgentExecutor
from aesculap.types import Route


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("v1\n")
    git("add", "-A")
    git("commit", "-m", "init")
    return repo


def res(name, status):
    return ProbeResult(name, status)


class FakeSuite:
    def __init__(self, snapshots):
        self._snaps = list(snapshots)
        self.calls = 0

    def run_all(self):
        snap = self._snaps[min(self.calls, len(self._snaps) - 1)]
        self.calls += 1
        return snap


def make_executor(git_repo, snapshots, runner, available=True):
    from aesculap.remediate.verify import Verifier
    ex = CodingAgentExecutor(
        tool="claude",
        verifier=Verifier(FakeSuite(snapshots)),
        git=GitBackupManager(str(git_repo)),
        command_template="claude -p {prompt}",
        runner=runner,
    )
    ex.available = lambda: available  # bypass real `which`
    return ex


def ok_runner(repo):
    def run(command):
        (repo / "app.py").write_text("v2-fixed\n")
        return subprocess.CompletedProcess(command, 0, "done", "")
    return run


def test_successful_fix_commits_and_passes(git_repo):
    before = [res("p", ProbeStatus.FAIL)]
    ex = make_executor(git_repo, [[res("p", ProbeStatus.OK)]], ok_runner(git_repo))
    result = ex.run("fix the bug", before)
    assert result.success
    assert result.next_route is Route.CODING_AGENT
    assert (git_repo / "app.py").read_text() == "v2-fixed\n"
    assert len(result.commit_sha) == 40


def test_verify_failure_rolls_back_to_human(git_repo):
    before = [res("p", ProbeStatus.FAIL)]
    ex = make_executor(git_repo, [[res("p", ProbeStatus.FAIL)]], ok_runner(git_repo))
    result = ex.run("fix the bug", before)
    assert not result.success
    assert result.next_route is Route.HUMAN
    assert (git_repo / "app.py").read_text() == "v1\n"  # rolled back


def test_tool_nonzero_exit_rolls_back(git_repo):
    before = [res("p", ProbeStatus.FAIL)]
    def bad_runner(command):
        (git_repo / "app.py").write_text("partial\n")
        return subprocess.CompletedProcess(command, 1, "", "tool error")
    ex = make_executor(git_repo, [[res("p", ProbeStatus.OK)]], bad_runner)
    result = ex.run("fix", before)
    assert not result.success
    assert result.next_route is Route.HUMAN
    assert (git_repo / "app.py").read_text() == "v1\n"


def test_unavailable_tool_to_human(git_repo):
    before = [res("p", ProbeStatus.FAIL)]
    ex = make_executor(git_repo, [[res("p", ProbeStatus.OK)]], ok_runner(git_repo),
                       available=False)
    result = ex.run("fix", before)
    assert not result.success
    assert result.next_route is Route.HUMAN


def test_command_built_with_prompt(git_repo):
    captured = {}
    def capture_runner(command):
        captured["cmd"] = command
        return subprocess.CompletedProcess(command, 0, "", "")
    before = [res("p", ProbeStatus.FAIL)]
    ex = make_executor(git_repo, [[res("p", ProbeStatus.OK)]], capture_runner)
    ex.run("repair config", before)
    assert "claude -p" in captured["cmd"]
    assert "repair config" in captured["cmd"]
