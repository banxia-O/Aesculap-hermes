"""Daemon wiring tests (PRD §2, §4, §12): event flow, de-bounce, lock."""

import pytest

from aesculap.audit.log import AuditLog
from aesculap.config import (
    Config,
    DebounceConfig,
    DetectorsConfig,
    ProbeConfig,
    ScopeConfig,
)
from aesculap.daemon import Daemon
from aesculap.events import DetectionEvent, EventSource
from aesculap.lockfile import FileLock, LockHeld


def make_config(tmp_path, **overrides):
    cfg = Config(
        scope=ScopeConfig(tier="A", project_root=str(tmp_path / "proj")),
        state_dir=str(tmp_path / "state"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        probes=[
            ProbeConfig(name="disk", type="disk_free",
                        params={"warn_percent": 200, "fail_percent": 300}),
        ],
        detectors=DetectorsConfig(log_paths=[], liveness_interval_seconds=999,
                                  full_checkup_interval_seconds=999),
        debounce=DebounceConfig(consecutive_threshold=2, recheck_seconds=60),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def ev(fp="log:x"):
    return DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint=fp, summary="s")


def test_event_below_threshold_not_confirmed(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    confirmed = []
    d = Daemon(cfg, audit, on_confirmed=confirmed.append)
    assert d.handle_event(ev()) is False
    assert confirmed == []


def test_event_confirmed_after_threshold(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    confirmed = []
    d = Daemon(cfg, audit, on_confirmed=confirmed.append)
    d.handle_event(ev())
    assert d.handle_event(ev()) is True
    assert len(confirmed) == 1


def test_drain_processes_queue(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    d = Daemon(cfg, audit)
    d.queue.put(ev())
    d.queue.put(ev())
    assert d.drain_once() == 2


def test_audit_records_detection(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    d = Daemon(cfg, audit)
    d.handle_event(ev())
    events = [r["event"] for r in audit.read_all()]
    assert "detection" in events


def test_default_confirmed_audits_fault(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    d = Daemon(cfg, audit)  # default handler
    d.handle_event(ev())
    d.handle_event(ev())
    events = [r["event"] for r in audit.read_all()]
    assert "fault_confirmed" in events


def test_disabled_daemon_does_not_lock(tmp_path):
    cfg = make_config(tmp_path, enabled=False)
    audit = AuditLog(cfg.audit_log_path)
    d = Daemon(cfg, audit)
    d.start()  # master switch off -> no lock, no detectors
    assert not d._lock.held
    events = [r["event"] for r in audit.read_all()]
    assert "daemon_disabled" in events


def test_lock_prevents_double_start(tmp_path):
    cfg = make_config(tmp_path)
    audit = AuditLog(cfg.audit_log_path)
    held = FileLock(cfg.state_dir.rstrip("/") + "/aesculap.lock")
    held.acquire()
    try:
        d = Daemon(cfg, audit)
        with pytest.raises(LockHeld):
            d.start()
    finally:
        held.release()


def test_liveness_probe_names_filtered(tmp_path):
    cfg = make_config(tmp_path)
    cfg.probes.append(
        ProbeConfig(name="proc", type="process_alive", params={"pattern": "x"})
    )
    audit = AuditLog(cfg.audit_log_path)
    d = Daemon(cfg, audit)
    # only process_alive/heartbeat count as liveness; disk_free does not
    assert d.liveness.probe_names == ["proc"]
