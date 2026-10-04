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
async def test_stream_agent_accumulates_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, messages, tools
        yield "token", {"text": "hola"}
        yield "usage", {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}

    recorder = TraceRecorder(organization_id="org", conversation_id="conv", call_id="call")
    events = [
        event
        async for event in stream_agent("hola", llm=FakeLLM(handler), trace=recorder)
    ]
    assert [kind for kind, _payload in events][-1] == "done"

    data = recorder.to_dict()
    assert data["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "llm_calls": 1,
    }


def test_call_summary_aggregates_token_usage() -> None:
    from types import SimpleNamespace

    from app.features.dev.router import _call_summary

    rows = [
        SimpleNamespace(
            status="ok",
            duration_ms=120,
            data={"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "llm_calls": 1}},
        ),
        SimpleNamespace(
            status="ok",
            duration_ms=80,
            data={"usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26, "llm_calls": 2}},
        ),
    ]
    summary = _call_summary(rows)

    assert summary["turns"] == 2
    assert summary["duration_ms"] == 200
    assert summary["prompt_tokens"] == 30
    assert summary["completion_tokens"] == 10
    assert summary["total_tokens"] == 40
    assert summary["llm_calls"] == 3
    assert summary["status"] == "ok"


@pytest.mark.asyncio
async def test_save_trace_skips_empty_recorder() -> None:
    await save_trace(None)
    await save_trace(TraceRecorder(organization_id="x", conversation_id="y"))
