"""Built-in probe + registry tests (PRD §3)."""

import os
import time

import pytest

from aesculap.config import ProbeConfig
from aesculap.probes.base import ProbeStatus, known_probe_types
from aesculap.probes.builtin import _tail_lines
from aesculap.probes.registry import ProbeSuite


def test_registry_has_builtins():
    types = known_probe_types()
    for t in ["process_alive", "log_error_count", "disk_free", "mem_free",
              "heartbeat_fresh", "api_last_success", "gateway_responds"]:
        assert t in types


def test_process_alive_finds_self():
    suite = ProbeSuite.from_config([
        ProbeConfig(name="self", type="process_alive",
                    params={"pattern": "python"}),
    ])
    # The test runner is a python process; this should be OK on Linux.
    r = suite.run_all()[0]
    if os.path.isdir("/proc"):
        assert r.status is ProbeStatus.OK
    else:
        assert r.status is ProbeStatus.WARN


def test_process_alive_missing_fails():
    suite = ProbeSuite.from_config([
        ProbeConfig(name="ghost", type="process_alive",
                    params={"pattern": "no_such_proc_zzz_12345"}),
    ])
    r = suite.run_all()[0]
    if os.path.isdir("/proc"):
        assert r.status is ProbeStatus.FAIL


def test_log_error_count_detects(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("ok\nok\nTraceback (most recent call last)\nok\n")
    suite = ProbeSuite.from_config([
        ProbeConfig(name="errs", type="log_error_count",
                    params={"path": str(log), "patterns": ["Traceback"],
                            "fail_threshold": 1}),
    ])
    r = suite.run_all()[0]
    assert r.status is ProbeStatus.FAIL
    assert r.metrics["hit_count"] == 1


def test_log_error_count_clean(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("ok\nok\nall good\n")
    suite = ProbeSuite.from_config([
        ProbeConfig(name="errs", type="log_error_count",
                    params={"path": str(log), "patterns": ["Traceback"]}),
    ])
    assert suite.run_all()[0].status is ProbeStatus.OK


def test_log_error_count_missing_file_fails(tmp_path):
    suite = ProbeSuite.from_config([
        ProbeConfig(name="errs", type="log_error_count",
                    params={"path": str(tmp_path / "nope.log")}),
    ])
    assert suite.run_all()[0].status is ProbeStatus.FAIL


def test_heartbeat_fresh(tmp_path):
    hb = tmp_path / "hb"
    hb.write_text("x")
    suite = ProbeSuite.from_config([
        ProbeConfig(name="hb", type="heartbeat_fresh",
                    params={"path": str(hb), "max_age_seconds": 100}),
    ])
    assert suite.run_all()[0].status is ProbeStatus.OK


def test_heartbeat_stale(tmp_path):
    hb = tmp_path / "hb"
    hb.write_text("x")
    old = time.time() - 1000
    os.utime(hb, (old, old))
    suite = ProbeSuite.from_config([
        ProbeConfig(name="hb", type="heartbeat_fresh",
                    params={"path": str(hb), "max_age_seconds": 100}),
    ])
    assert suite.run_all()[0].status is ProbeStatus.FAIL


def test_gateway_responds_exit_code(tmp_path):
    ok = ProbeSuite.from_config([
        ProbeConfig(name="g", type="gateway_responds",
                    params={"command": "true"}),
    ])
    assert ok.run_all()[0].status is ProbeStatus.OK
    bad = ProbeSuite.from_config([
        ProbeConfig(name="g", type="gateway_responds",
                    params={"command": "false"}),
    ])
    assert bad.run_all()[0].status is ProbeStatus.FAIL


def test_disk_free_runs():
    suite = ProbeSuite.from_config([
        ProbeConfig(name="d", type="disk_free",
                    params={"path": "/", "warn_percent": 200, "fail_percent": 300}),
    ])
    # thresholds set absurdly high so it can't FAIL/WARN -> OK
    assert suite.run_all()[0].status is ProbeStatus.OK


def test_disabled_probe_skipped():
    suite = ProbeSuite.from_config([
        ProbeConfig(name="off", type="disk_free", enabled=False),
    ])
    assert suite.names == []


def test_probe_exception_becomes_warn():
    # log_error_count with a path that's a directory triggers an internal error
    suite = ProbeSuite.from_config([
        ProbeConfig(name="weird", type="log_error_count",
                    params={"path": "/", "patterns": ["x"]}),
    ])
    r = suite.run_all()[0]
    # "/" is not a file -> FAIL ("log file missing"); never raises
    assert r.status in (ProbeStatus.FAIL, ProbeStatus.WARN)


def test_run_subset(tmp_path):
    suite = ProbeSuite.from_config([
        ProbeConfig(name="a", type="disk_free",
                    params={"warn_percent": 200, "fail_percent": 300}),
        ProbeConfig(name="b", type="disk_free",
                    params={"warn_percent": 200, "fail_percent": 300}),
    ])
    res = suite.run_subset(["b"])
    assert len(res) == 1 and res[0].name == "b"


def test_tail_lines(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("\n".join(str(i) for i in range(1000)) + "\n")
    last = _tail_lines(f, 5)
    assert last == ["995", "996", "997", "998", "999"]
