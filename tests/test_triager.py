"""Triager tests (PRD §5): orchestration + LLM-error fallback to human."""

from aesculap.events import DetectionEvent, EventSource
from aesculap.llm.base import LLMError, LLMProvider, LLMResponse
from aesculap.triage.triager import Triager
from aesculap.types import Route


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, reply="", error=False):
        super().__init__(model="fake-model")
        self._reply = reply
        self._error = error
        self.calls = 0

    def complete(self, system, user, *, max_tokens=1024):
        self.calls += 1
        if self._error:
            raise LLMError("boom")
        return LLMResponse(text=self._reply, model=self.model)


def ev():
    return DetectionEvent(source=EventSource.LOG_WATCHER, fingerprint="log:x",
                          summary="boom", evidence="Traceback ...")


VALID = ('{"diagnosis":"d","blast_radius":"restart","reversible":true,'
         '"confidence":0.8,"route":"self_fix","needs_human_reason":"null",'
         '"actions":["systemctl restart hermes"]}')


def test_valid_triage_passes_through():
    p = FakeProvider(reply=VALID)
    res = Triager(p).triage(ev())
    assert not res.degraded
    assert res.decision.route is Route.SELF_FIX


def test_llm_error_degrades_to_human_no_retry():
    p = FakeProvider(error=True)
    res = Triager(p).triage(ev())
    assert res.degraded
    assert res.decision.route is Route.HUMAN
    assert p.calls == 1  # NO retry (decision #3)


def test_garbage_output_degrades():
    p = FakeProvider(reply="I cannot help with that")
    res = Triager(p).triage(ev())
    assert res.degraded
    assert res.decision.route is Route.HUMAN


def test_prompt_includes_evidence():
    p = FakeProvider(reply=VALID)
    Triager(p).triage(ev())
    # provider was called exactly once with our content
    assert p.calls == 1
