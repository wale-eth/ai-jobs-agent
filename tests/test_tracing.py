"""Tracing must work with keys present (offline) and without keys.

The Langfuse SDK buffers spans asynchronously, so creating observations with
dummy keys and no network must not raise; a regression here would crash
every scheduled sweep the moment secrets are configured.
"""

from jobs_agent.tracing import observe_llm, sweep_trace


class FakeResponse:
    class _Usage:
        input_tokens = 3
        output_tokens = 2

    def __init__(self):
        self.content = [type("Block", (), {"text": "hello"})()]
        self.usage = self._Usage()


def test_noop_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with sweep_trace("test-sweep"):
        response = observe_llm("gen", "model-x", FakeResponse)
    assert response.content[0].text == "hello"


def test_active_with_dummy_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:9")  # unreachable
    with sweep_trace("test-sweep", metadata={"db": "x"}):
        response = observe_llm("gen", "model-x", FakeResponse)
    assert response.content[0].text == "hello"
