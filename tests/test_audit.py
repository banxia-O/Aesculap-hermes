"""Audit log tests (PRD §13): append-only JSONL, round-trips dataclasses."""

from aesculap.audit.log import AuditLog
from aesculap.types import BlastRadius, GateDecision, Route


def test_append_and_readback(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("detection", probe="process_alive", status="FAIL")
    log.record("triage", route="self_fix")
    records = log.read_all()
    assert len(records) == 2
    assert records[0]["event"] == "detection"
    assert records[1]["route"] == "self_fix"


def test_append_only_accumulates(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("a", n=1)
    # a fresh instance pointing at the same path must not truncate
    AuditLog(path).record("b", n=2)
    assert len(AuditLog(path).read_all()) == 2


def test_dataclass_and_enum_serialized(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    decision = GateDecision(
        final_route=Route.HUMAN,
        overridden=True,
        proposed_route=Route.SELF_FIX,
        reasons=["tripwire"],
    )
    rec = log.record("gate", decision=decision, radius=BlastRadius.INFRA)
    assert rec["decision"]["final_route"] == "human"
    assert rec["decision"]["proposed_route"] == "self_fix"
    assert rec["radius"] == "infra"


def test_records_have_timestamps(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    rec = log.record("x")
    assert "ts" in rec and "iso" in rec
