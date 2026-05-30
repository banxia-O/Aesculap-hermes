"""Notifier tests (PRD §8.3, §11): channel send, de-dup, gateway fallback."""

import subprocess

from aesculap.notify.dedup import NotificationDeduper
from aesculap.notify.message import NotificationMessage
from aesculap.notify.notifier import Notifier


def msg():
    return NotificationMessage(title="t", body="b")


def ok_runner(captured):
    def run(command):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")
    return run


def fail_runner(command):
    return subprocess.CompletedProcess(command, 1, "", "gateway down")


def test_send_success(tmp_path):
    captured = []
    n = Notifier("send {message}", runner=ok_runner(captured))
    res = n.notify("fp1", msg())
    assert res.sent
    assert "send " in captured[0]


def test_template_without_placeholder_appends(tmp_path):
    captured = []
    n = Notifier("mycmd", runner=ok_runner(captured))
    n.notify("fp1", msg())
    assert captured[0].startswith("mycmd ")


def test_gateway_failure_returns_not_sent():
    n = Notifier("send {message}", runner=fail_runner)
    res = n.notify("fp1", msg())
    assert not res.sent
    assert "exit 1" in res.reason


def test_send_exception_handled():
    def boom(command):
        raise OSError("no such command")
    n = Notifier("send {message}", runner=boom)
    res = n.notify("fp1", msg())
    assert not res.sent
    assert "failed" in res.reason


def test_dedup_suppresses_second(tmp_path):
    captured = []
    deduper = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    n = Notifier("send {message}", deduper=deduper, runner=ok_runner(captured))
    first = n.notify("fp1", msg())
    second = n.notify("fp1", msg())
    assert first.sent
    assert second.suppressed
    assert len(captured) == 1  # only sent once


def test_dedup_marks_only_on_success(tmp_path):
    deduper = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    n = Notifier("send {message}", deduper=deduper, runner=fail_runner)
    n.notify("fp1", msg())
    # send failed -> not marked -> next attempt still allowed
    assert deduper.should_notify("fp1")
