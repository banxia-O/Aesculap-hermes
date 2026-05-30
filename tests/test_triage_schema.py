"""Triage schema parsing tests (PRD §5.2, decision #3).

The safety property: malformed / illegal / incomplete triage output degrades
deterministically to route=human. No guessing, no retry.
"""

import json

from aesculap.triage.schema import parse_triage
from aesculap.types import BlastRadius, NeedsHumanReason, Route


def valid_json(**over):
    base = {
        "diagnosis": "process died",
        "blast_radius": "restart",
        "reversible": True,
        "confidence": 0.9,
        "route": "self_fix",
        "needs_human_reason": "null",
        "actions": ["systemctl restart hermes"],
    }
    base.update(over)
    return json.dumps(base)


def test_valid_parses():
    out = parse_triage(valid_json())
    assert not out.degraded
    assert out.decision.route is Route.SELF_FIX
    assert out.decision.blast_radius is BlastRadius.RESTART
    assert out.decision.actions == ["systemctl restart hermes"]


def test_structured_write_file_action_parses():
    out = parse_triage(valid_json(actions=[
        {"kind": "write_file", "path": "/p/app.cfg", "content": "k: v\n"},
    ]))
    assert not out.degraded
    assert out.decision.actions == [
        {"kind": "write_file", "path": "/p/app.cfg", "content": "k: v\n"}
    ]


def test_mixed_string_and_structured_actions():
    out = parse_triage(valid_json(actions=[
        "restart hermes",
        {"kind": "write_file", "path": "/p/x", "content": "y"},
    ]))
    assert not out.degraded
    assert out.decision.actions[0] == "restart hermes"
    assert out.decision.actions[1]["kind"] == "write_file"


def test_write_file_missing_content_degrades():
    out = parse_triage(valid_json(actions=[{"kind": "write_file", "path": "/p/x"}]))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_write_file_missing_path_degrades():
    out = parse_triage(valid_json(actions=[{"kind": "write_file", "content": "y"}]))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_unknown_structured_action_kind_degrades():
    out = parse_triage(valid_json(actions=[{"kind": "deploy", "target": "prod"}]))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_non_string_non_dict_action_degrades():
    out = parse_triage(valid_json(actions=[123]))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_unparseable_degrades_to_human():
    out = parse_triage("this is not json at all")
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_broken_json_degrades():
    out = parse_triage('{"route": "self_fix", ')  # truncated
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_missing_route_degrades():
    out = parse_triage(json.dumps({"diagnosis": "x", "blast_radius": "restart"}))
    assert out.degraded
    assert out.decision.route is Route.HUMAN
    assert "route" in out.reason


def test_illegal_route_degrades():
    out = parse_triage(valid_json(route="please_fix_everything"))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_illegal_blast_radius_degrades():
    out = parse_triage(valid_json(blast_radius="apocalyptic"))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_non_bool_reversible_degrades():
    out = parse_triage(valid_json(reversible="yes"))
    assert out.degraded
    assert out.decision.route is Route.HUMAN


def test_non_object_degrades():
    out = parse_triage(json.dumps([1, 2, 3]))
    assert out.degraded


def test_actions_not_list_degrades():
    out = parse_triage(valid_json(actions="restart it"))
    assert out.degraded


def test_missing_blast_radius_defaults_unknown():
    payload = json.dumps({"route": "self_fix", "reversible": True})
    out = parse_triage(payload)
    assert not out.degraded
    assert out.decision.blast_radius is BlastRadius.UNKNOWN


def test_json_in_code_fence():
    text = "Here is my analysis:\n```json\n" + valid_json() + "\n```\nDone."
    out = parse_triage(text)
    assert not out.degraded
    assert out.decision.route is Route.SELF_FIX


def test_json_embedded_in_prose():
    text = "I think: " + valid_json() + " that's my call."
    out = parse_triage(text)
    assert not out.degraded


def test_bad_confidence_coerced_not_fatal():
    out = parse_triage(valid_json(confidence="high"))
    assert not out.degraded  # confidence is record-only; bad value -> 0.0
    assert out.decision.confidence == 0.0


def test_human_route_with_reason():
    out = parse_triage(valid_json(route="human", needs_human_reason="missing_key"))
    assert not out.degraded
    assert out.decision.route is Route.HUMAN
    assert out.decision.needs_human_reason is NeedsHumanReason.MISSING_KEY


def test_illegal_needs_human_reason_degrades():
    out = parse_triage(valid_json(needs_human_reason="aliens"))
    assert out.degraded
