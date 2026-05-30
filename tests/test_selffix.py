"""self_fix executor tests (PRD §7): backup/verify/observe/rollback/budget."""

import subprocess

from aesculap.gate.escalation import EscalationLadder, EscalationState
from aesculap.probes.base import ProbeResult, ProbeStatus
from aesculap.remediate.backup import FileBackupManager
from aesculap.remediate.selffix import SelfFixExecutor
from aesculap.types import ActionKind, ProposedAction, Route


class FakeSuite:
    """A probe suite whose run_all() returns scripted snapshots in sequence."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    def run_all(self):
        snap = self._snapshots[min(self.calls, len(self._snapshots) - 1)]
        self.calls += 1
        return snap


def res(name, status):
    return ProbeResult(name, status)


def ok_proc():
    return subprocess.CompletedProcess(args="x", returncode=0, stdout="", stderr="")


def fail_proc():
    return subprocess.CompletedProcess(args="x", returncode=1, stdout="", stderr="boom")


def make_executor(snapshots, tmp_path, restart_ok=True, budget=3, observe=0):
    from aesculap.remediate.verify import Verifier
    suite = FakeSuite(snapshots)
    verifier = Verifier(suite)
    mgr = FileBackupManager(str(tmp_path / "backups"))
    ladder = EscalationLadder(retry_budget=budget, coding_agent_available=True)
    ex = SelfFixExecutor(
        verifier, mgr, ladder,
        restart_fn=lambda a: ok_proc() if restart_ok else fail_proc(),
        command_fn=lambda a: ok_proc(),
        observe_window_seconds=observe,
        sleep_fn=lambda s: None,
    )
    return ex, suite


def restart_action():
    return [ProposedAction(kind=ActionKind.RESTART_PROCESS, description="restart")]


def test_successful_restart_fix(tmp_path):
    # before: FAIL; after verify: OK
    before = [res("proc", ProbeStatus.FAIL)]
    ex, suite = make_executor([[res("proc", ProbeStatus.OK)]], tmp_path)
    state = EscalationState()
    result = ex.run(restart_action(), before, state)
    assert result.success
    assert result.next_route is Route.SELF_FIX


def test_observation_window_reverify(tmp_path):
    before = [res("proc", ProbeStatus.FAIL)]
    # first verify OK, observation re-verify also OK
    ex, suite = make_executor(
        [[res("proc", ProbeStatus.OK)], [res("proc", ProbeStatus.OK)]],
        tmp_path, observe=30)
    result = ex.run(restart_action(), before, EscalationState())
    assert result.success
    assert suite.calls == 2  # verified twice (immediate + observation)


def test_observation_window_regression_fails(tmp_path):
    before = [res("proc", ProbeStatus.FAIL)]
    # first verify OK, but observation re-verify regresses to FAIL
    ex, suite = make_executor(
        [[res("proc", ProbeStatus.OK)], [res("proc", ProbeStatus.FAIL)]],
        tmp_path, observe=30)
    result = ex.run(restart_action(), before, EscalationState())
    assert not result.success


def test_action_failure_rolls_back_and_counts(tmp_path):
    before = [res("proc", ProbeStatus.FAIL)]
    ex, suite = make_executor([[res("proc", ProbeStatus.OK)]], tmp_path,
                              restart_ok=False)
    state = EscalationState()
    result = ex.run(restart_action(), before, state)
    assert not result.success
    assert state.self_fix_attempts == 1


def test_verify_failure_counts_against_budget(tmp_path):
    before = [res("proc", ProbeStatus.FAIL)]
    # action succeeds but verify still shows FAIL
    ex, suite = make_executor([[res("proc", ProbeStatus.FAIL)]], tmp_path)
    state = EscalationState()
    result = ex.run(restart_action(), before, state)
    assert not result.success
    assert state.self_fix_attempts == 1
    assert result.next_route is Route.SELF_FIX  # budget 3, still room


def test_budget_exhaustion_escalates(tmp_path):
    before = [res("proc", ProbeStatus.FAIL)]
    ex, suite = make_executor([[res("proc", ProbeStatus.FAIL)]], tmp_path,
                              budget=1)
    state = EscalationState()
    result = ex.run(restart_action(), before, state)
    assert result.next_route is Route.CODING_AGENT  # budget 1 exhausted


def test_default_command_does_not_invoke_shell(tmp_path):
    """SECURITY: _default_command must run with shell=False so that shell
    metacharacters stay inert literals and command substitution cannot execute."""
    from aesculap.remediate.selffix import _default_command

    action = ProposedAction(kind=ActionKind.RUN_COMMAND,
                            command="echo $(echo INJECTED)")
    proc = _default_command(action)
    # Under shell=True this would expand to "INJECTED"; under shell=False the
    # `echo` binary prints the substitution syntax verbatim.
    assert proc.stdout.strip() == "$(echo INJECTED)"


def test_default_command_empty_is_safe(tmp_path):
    from aesculap.remediate.selffix import _default_command

    proc = _default_command(ProposedAction(kind=ActionKind.RUN_COMMAND, command=""))
    assert proc.returncode == 1  # no crash on empty argv


def test_file_backup_restored_on_verify_failure(tmp_path):
    conf = tmp_path / "c.json"
    conf.write_text("orig")
    before = [res("proc", ProbeStatus.FAIL)]
    ex, suite = make_executor([[res("proc", ProbeStatus.FAIL)]], tmp_path)

    # WRITE_FILE writes its content directly (no shell); verify then fails, so
    # the original content must be restored.
    action = [ProposedAction(kind=ActionKind.WRITE_FILE, path=str(conf),
                             content="edited")]
    ex.run(action, before, EscalationState())
    assert conf.read_text() == "orig"  # rolled back


def test_write_file_applies_content_without_shell(tmp_path):
    conf = tmp_path / "c.json"
    conf.write_text("orig")
    before = [res("proc", ProbeStatus.FAIL)]
    # verify passes (proc OK after the write)
    ex, suite = make_executor([[res("proc", ProbeStatus.OK)]], tmp_path)
    action = [ProposedAction(kind=ActionKind.WRITE_FILE, path=str(conf),
                             content="fixed: true\n")]
    result = ex.run(action, before, EscalationState())
    assert result.success
    assert conf.read_text() == "fixed: true\n"


def test_write_file_refused_outside_scope(tmp_path):
    """When a ScopeGate is wired, a write outside scope is refused at execution
    (defense-in-depth) and does not touch the target."""
    from aesculap.config import ScopeConfig
    from aesculap.gate.scope import ScopeGate

    project = tmp_path / "proj"
    project.mkdir()
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    before = [res("proc", ProbeStatus.FAIL)]
    ex, suite = make_executor([[res("proc", ProbeStatus.FAIL)]], tmp_path)
    ex.scope = scope

    target = tmp_path / "outside.txt"  # outside the project tree
    action = [ProposedAction(kind=ActionKind.WRITE_FILE, path=str(target),
                             content="x")]
    result = ex.run(action, before, EscalationState())
    assert not result.success
    assert not target.exists()
