from __future__ import annotations

import pytest

from app.features.agent.service import stream_agent
from app.platform.tracing import TraceRecorder, save_trace
from app.providers import FakeLLM


@pytest.mark.asyncio
async def test_stream_agent_populates_trace_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt
        if any(message.get("role") == "tool" for message in messages or []):
            yield "token", {"text": "7"}
            return
        yield "tool_calls", {
            "calls": [{"id": "call_1", "name": "sum_numbers", "arguments": '{"numbers":[3,4]}'}]
        }

    recorder = TraceRecorder(
        organization_id="0b90425d-b5b6-4013-a7ec-7dc19662b926",
        conversation_id="0b90425d-b5b6-4013-a7ec-7dc19662b927",
        call_id="0b90425d-b5b6-4013-a7ec-7dc19662b928",
    )
    events = [
        event
        async for event in stream_agent(
            "suma 3 y 4",
            llm=FakeLLM(handler, supports_tools=True),
            trace=recorder,
        )
    ]
    assert [kind for kind, _payload in events][-1] == "done"

    data = recorder.to_dict()
    names = [span["name"] for span in data["spans"]]
    assert names[0] == "agent.turn"
    assert "rag.retrieve" in names
    assert names.count("llm.request") == 2
    assert "tool.sum_numbers" in names
    assert data["status"] == "ok"
    assert data["answer"] == "7"

    tool_span = next(span for span in data["spans"] if span["name"] == "tool.sum_numbers")
    assert tool_span["attributes"]["ok"] is True
    assert tool_span["attributes"]["result"] == "7"
    assert tool_span["attributes"]["arguments"] == {"numbers": [3, 4]}


@pytest.mark.asyncio
async def test_save_trace_skips_empty_recorder() -> None:
    await save_trace(None)
    await save_trace(TraceRecorder(organization_id="x", conversation_id="y"))
