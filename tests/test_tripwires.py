"""Hard tripwire tests (PRD §8.1).

Command-shaped and file-shaped actions that must be intercepted regardless of
what the LLM proposed.
"""

import pytest

from aesculap.config import ScopeConfig
from aesculap.gate.scope import ScopeGate
from aesculap.gate.tripwires import TripwireGate
from aesculap.types import ActionKind, ProposedAction


@pytest.fixture
def gate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    scope = ScopeGate(ScopeConfig(tier="A", project_root=str(project)))
    return TripwireGate(scope), project


def act_cmd(command):
    return ProposedAction(kind=ActionKind.RUN_COMMAND, command=command)


def act_write(path):
    return ProposedAction(kind=ActionKind.WRITE_FILE, path=str(path))


@pytest.mark.parametrize("command", [
    "rm -rf /home/hermes",
    "rm file.txt",
    "git push --force origin main",
    "git push -f",
    "shred -u secrets",
    "dd if=/dev/zero of=/dev/sda",
])
def test_forbidden_commands_fire(gate, command):
    tg, _ = gate
    hits = tg.scan([act_cmd(command)])
    assert hits, f"expected tripwire for {command!r}"


@pytest.mark.parametrize("command", [
    "curl https://api.example.com/billing/charge",
    "POST /v1/payment",
    "buy more credits via checkout",
])
def test_billing_interface_fires(gate, command):
    tg, _ = gate
    assert tg.scan([act_cmd(command)])


def test_benign_command_passes(gate):
    tg, _ = gate
    assert tg.scan([act_cmd("systemctl restart hermes")]) == []


def test_write_outside_scope_fires(gate):
    tg, project = gate
    hits = tg.scan([act_write("/etc/passwd")])
    assert hits


def test_write_inside_scope_passes(gate):
    tg, project = gate
    assert tg.scan([act_write(project / "app.py")]) == []


def test_delete_file_is_destructive(gate):
    tg, project = gate
    a = ProposedAction(kind=ActionKind.DELETE_FILE, path=str(project / "x.txt"))
    assert tg.scan([a])


def test_file_action_without_path_fails_closed(gate):
    tg, _ = gate
    a = ProposedAction(kind=ActionKind.WRITE_FILE, path=None)
    assert tg.scan([a])


def test_multiple_actions_report_indices(gate):
    tg, project = gate
    actions = [
        act_write(project / "ok.py"),       # clean
        act_cmd("rm -rf /"),                # fires
    ]
    hits = tg.scan(actions)
    assert len(hits) == 1
    assert hits[0].action_index == 1
