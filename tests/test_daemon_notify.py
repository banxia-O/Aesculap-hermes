"""Daemon -> notifier integration (PRD §8.3, §11).

A confirmed fault routed to human must produce an actionable, key-safe
notification through the configured channel.
"""

import subprocess

from aesculap.audit.log import AuditLog
from aesculap.config import (
    Config,
    DetectorsConfig,
    NotifyConfig,
    ProbeConfig,
    ScopeConfig,
    TriageConfig,
)
from aesculap.daemon import Daemon
from aesculap.events import DetectionEvent, EventSource
from aesculap.gate.blast_radius import BlastRadiusGate
from aesculap.gate.scope import ScopeGate
from aesculap.gate.tripwires import TripwireGate
from aesculap.llm.base import LLMProvider, LLMResponse
from aesculap.pipeline import Pipeline
from aesculap.triage.triager import Triager


class FakeProvider(LLMProvider):
    def __init__(self, reply):
        super().__init__(model="fake")
        self._reply = reply

    def complete(self, system, user, *, max_tokens=1024):
        return LLMResponse(text=self._reply, model=self.model)


def make_daemon(tmp_path, sent_box):
    cfg = Config(
        scope=ScopeConfig(tier="A", project_root=str(tmp_path / "proj")),
        state_dir=str(tmp_path / "state"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        mode="fix",
        probes=[],
        detectors=DetectorsConfig(log_paths=[]),
        triage=TriageConfig(provider="anthropic", model="m"),
        notify=NotifyConfig(command_template="send {message}"),
    )
    audit = AuditLog(cfg.audit_log_path)
    daemon = Daemon(cfg, audit)
    # Replace the notifier's runner so nothing actually shells out.
    def runner(command):
        sent_box.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    daemon.notifier._runner = runner
    return daemon, audit


def human_triage_json():
    # 401 -> missing key -> human, with a secret in the evidence to scrub
    return ('{"diagnosis":"key expired","blast_radius":"infra",'
            '"reversible":false,"route":"human",'
            '"needs_human_reason":"missing_key","actions":[]}')


def test_human_escalation_sends_notification(tmp_path):
    sent = []
    daemon, audit = make_daemon(tmp_path, sent)
    # Patch the triager to our fake reply.
    daemon.pipeline.triager = Triager(FakeProvider(human_triage_json()))
    event = DetectionEvent(
        source=EventSource.LOG_WATCHER, fingerprint="log:x",
        summary="auth failure", evidence="401 unauthorized sk-secret12345678",
        related_probes=["api"],
    )
    daemon._pipeline_confirmed(event)
    assert len(sent) == 1
    # key-safety: the secret must not appear in the sent message
    assert "sk-secret12345678" not in sent[0]
    events = [r["event"] for r in audit.read_all()]
    assert "notify_human" in events


def test_observe_mode_does_not_notify(tmp_path):
    sent = []
    daemon, audit = make_daemon(tmp_path, sent)
    daemon.remediation.mode = "observe"
    daemon.pipeline.triager = Triager(FakeProvider(human_triage_json()))
    event = DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint="log:y",
                           summary="auth failure")
    daemon._pipeline_confirmed(event)
    assert sent == []  # observe never acts/notifies via remediation
