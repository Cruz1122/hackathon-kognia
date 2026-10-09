from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.features.agent.service import stream_agent
from app.platform.tracing import TraceRecorder, save_trace
from app.providers import FakeLLM


@pytest.mark.asyncio
async def test_stream_agent_populates_trace_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    from app.domains.ips import tools as ips_tools

    async def no_snapshot():
        return None

    monkeypatch.setattr(ips_tools.repository, "active_snapshot", no_snapshot)

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt
        if any(message.get("role") == "tool" for message in messages or []):
            yield "token", {"text": "7"}
            return
        yield "tool_calls", {
            "calls": [{"id": "call_1", "name": "search_ips", "arguments": '{"query":"Hospital"}'}]
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
    assert "tool.search_ips" in names
    assert data["status"] == "ok"
    assert data["answer"] == "7"

    tool_span = next(span for span in data["spans"] if span["name"] == "tool.search_ips")
    assert tool_span["attributes"]["ok"] is True
    assert "no_active_snapshot" in tool_span["attributes"]["result"]
    assert tool_span["attributes"]["arguments"] == {"query": "Hospital"}


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
    from app.features.dev.router import _call_summary

    rows = [
        SimpleNamespace(
            status="ok",
            duration_ms=120,
            model="gpt-4o-mini",
            data={
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "llm_calls": 1},
                "spans": [{"name": "tool.lookup"}, {"name": "rag.retrieve"}],
            },
        ),
        SimpleNamespace(
            status="ok",
            duration_ms=80,
            model="gpt-4o-mini",
            data={
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26, "llm_calls": 2},
                "spans": [{"name": "tool.search_ips"}],
            },
        ),
    ]
    summary = _call_summary(rows)

    assert summary["turns"] == 2
    assert summary["duration_ms"] == 200
    assert summary["prompt_tokens"] == 30
    assert summary["completion_tokens"] == 10
    assert summary["total_tokens"] == 40
    assert summary["llm_calls"] == 3
    assert summary["tools"] == 2
    assert summary["rag_calls"] == 1
    assert summary["status"] == "ok"
    # gpt-4o-mini: 30 in / 10 out → 30/1e6*0.15 + 10/1e6*0.60
    assert summary["cost_usd"] == pytest.approx(0.0000105, abs=1e-6)


def test_trace_payload_keeps_full_application_text() -> None:
    recorder = TraceRecorder(organization_id="org", conversation_id="conv", call_id="call")
    long_text = "x" * 3000
    recorder.start(long_text, [{"role": "user", "content": long_text, "tool_call_id": "tool-1"}], [])
    llm = recorder.start_llm(
        provider="test",
        model="model",
        attempt=1,
        round_index=0,
        messages=[{"role": "user", "content": long_text, "tool_call_id": "tool-1"}],
        tools=[],
    )
    recorder.note_text(llm, long_text)
    recorder.close_tool(recorder.start_tool(name="lookup", tool_call_id="tool-1", arguments={"value": long_text}), result=long_text, ok=True, inputs=[], outputs=[])
    data = recorder.to_dict()
    assert data["history"][0]["content"] == long_text
    assert data["spans"][1]["attributes"]["messages"][0]["content"] == long_text
    assert data["spans"][1]["attributes"]["text"] == long_text
    assert data["spans"][2]["attributes"]["result"] == long_text


def test_trace_payload_keeps_response_separate_from_llm_payload() -> None:
    recorder = TraceRecorder(organization_id="org", conversation_id="conv", call_id="call")
    recorder.start("hola", [], [])
    span = recorder.start_llm(
        provider="test",
        model="model",
        attempt=1,
        round_index=1,
        messages=[{"role": "user", "content": "hola"}],
        tools=[],
    )
    recorder.note_text(span, "respuesta")
    recorder.note_usage(span, prompt_tokens=2, completion_tokens=3, total_tokens=5)
    recorder.close_span(span, tool_calls=[])
    payload = next(item for item in recorder.to_dict()["spans"] if item["name"] == "llm.request")
    assert payload["attributes"]["messages"] != payload["attributes"]["response"]
    assert payload["attributes"]["response"] == {
        "text": "respuesta",
        "tool_calls": [],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }


def test_trace_capture_limits_are_explicit() -> None:
    recorder = TraceRecorder(organization_id="org", conversation_id="conv", call_id="call")
    history = [{"role": "user", "content": str(index)} for index in range(45)]
    recorder.start("prompt", history, [])
    recorder.start_llm(
        provider="test",
        model="model",
        attempt=1,
        round_index=0,
        messages=history,
        tools=[],
    )
    hits = [SimpleNamespace(content=f"hit-{index}") for index in range(10)]
    recorder.record_retrieval(used_rag=True, topic="topic", hits=hits)
    data = recorder.to_dict()
    assert data["history_limited"] is True
    assert data["history_total"] == 45
    llm_attributes = data["spans"][1]["attributes"]
    assert llm_attributes["messages_limited"] is True
    assert llm_attributes["messages_total"] == 45
    rag_attributes = data["spans"][2]["attributes"]
    assert rag_attributes["hits_limited"] is True
    assert rag_attributes["hits_total"] == 10


def test_dev_replay_events_align_trace_and_post_call_messages() -> None:
    from app.features.dev.router import _agent_event_turn_id, _call_event_view, _message_event_view, _trace_event_views

    started = datetime(2026, 1, 1, tzinfo=UTC)
    trace = SimpleNamespace(
        id="trace-1",
        started_at=started + timedelta(seconds=2),
        data={
            "spans": [{
                "name": "llm.request",
                "start_ms": 250,
                "duration_ms": 1500,
                "attributes": {
                    "first_token_ms": 300,
                    "total_tokens": 10,
                    "response": {"text": "Respuesta", "tool_calls": []},
                },
            }],
        },
    )
    events = _trace_event_views(trace, call_started_at=started, recording_offset_ms=1000, recording_duration_ms=5000)
    assert events[0]["offset_ms"] == 2250
    assert events[0]["playback_ms"] == 1250
    assert events[0]["response"] == {"text": "Respuesta", "tool_calls": []}
    assert events[0]["response"] != events[0]["payload"]
    assert "response" not in events[0]["payload"]["attributes"]
    message = SimpleNamespace(
        id="message-1",
        channel="whatsapp",
        role="assistant",
        content="Seguimos por WhatsApp",
        created_at=started + timedelta(seconds=12),
    )
    post_call = _message_event_view(message, call_started_at=started, recording_offset_ms=1000, recording_duration_ms=5000, order=0)
    assert post_call["offset_ms"] == 12000
    assert post_call["playback_ms"] == 5000


def test_agent_transcript_matching_skips_untraced_greeting() -> None:
    from app.features.dev.router import _agent_event_turn_id, _call_event_view

    started = datetime(2026, 1, 1, tzinfo=UTC)
    traces = [
        SimpleNamespace(id="turn-1", started_at=started + timedelta(seconds=10)),
        SimpleNamespace(id="turn-2", started_at=started + timedelta(seconds=20)),
    ]
    greeting = SimpleNamespace(
        id="event-greeting",
        event_type="transcript.final",
        occurred_at=started + timedelta(seconds=5),
        payload={"speaker": "agent", "text": "Hola"},
    )
    first = SimpleNamespace(
        id="event-first",
        seq=2,
        event_type="transcript.final",
        occurred_at=started + timedelta(seconds=12),
        offset_ms=12000,
        payload={"speaker": "agent", "text": "Primera respuesta"},
    )
    second = SimpleNamespace(
        id="event-second",
        event_type="transcript.final",
        occurred_at=started + timedelta(seconds=22),
        payload={"speaker": "agent", "text": "Segunda respuesta"},
    )

    assert _agent_event_turn_id(greeting, traces) is None
    assert _agent_event_turn_id(first, traces) == "turn-1"
    assert _agent_event_turn_id(second, traces) == "turn-2"
    view = _call_event_view(first, recording_offset_ms=0, recording_duration_ms=1000, turn_id="turn-1")
    assert view["turn_id"] == "turn-1"


def test_turn_view_recovers_answer_from_observable_llm_span() -> None:
    from app.features.dev.router import _turn_view

    row = SimpleNamespace(
        id="trace-1",
        provider="openrouter",
        model="model",
        status="ok",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_ms=100,
        data={
            "answer": "",
            "spans": [
                {"name": "llm.request", "attributes": {"text": "Respuesta observable"}},
            ],
        },
    )

    assert _turn_view(row)["answer"] == "Respuesta observable"


def test_turn_view_uses_agent_transcript_when_historical_trace_is_empty() -> None:
    from app.features.dev.router import _turn_view

    row = SimpleNamespace(
        id="trace-1",
        provider=None,
        model=None,
        status="ok",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_ms=100,
        data={"answer": "", "spans": []},
    )

    view = _turn_view(row, "Respuesta guardada en transcript")
    assert view["answer"] == "Respuesta guardada en transcript"
    assert view["data"]["answer"] == "Respuesta guardada en transcript"


def test_estimate_cost_uses_published_prices() -> None:
    from app.platform import pricing

    pricing.reset_remote_cache()
    assert pricing.price_for("gpt-4o-mini") is not None
    assert pricing.price_for("minimax/minimax-m2.7") is not None
    assert pricing.price_for("unknown-model") is None
    # 1M in + 1M out at gpt-4o-mini → $0.15 + $0.60
    assert pricing.estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
    assert pricing.estimate_cost_usd("unknown-model", 1000, 1000) is None


def test_remote_catalogue_overrides_fallback_and_maps_aliases() -> None:
    from app.platform import pricing

    pricing.reset_remote_cache()
    catalogue = pricing._parse_catalogue(
        {
            "data": [
                {
                    "id": "openai/gpt-4o-mini",
                    "pricing": {"prompt": "0.0000002", "completion": "0.0000008", "input_cache_read": "0.0000001"},
                }
            ]
        }
    )
    remote = pricing._RemoteCache()
    remote.prices = catalogue
    remote.loaded = True
    pricing._remote_cache = remote

    price = pricing.price_for("gpt-4o-mini")
    assert price is not None
    assert price.input_per_million == pytest.approx(0.20)
    assert price.output_per_million == pytest.approx(0.80)
    assert price.cached_input_per_million == pytest.approx(0.10)

    pricing.reset_remote_cache()
    # Without the remote catalogue, the hardcoded fallback still resolves.
    fallback = pricing.price_for("gpt-4o-mini")
    assert fallback is not None and fallback.input_per_million == 0.15


@pytest.mark.asyncio
async def test_save_trace_skips_empty_recorder() -> None:
    await save_trace(None)
    await save_trace(TraceRecorder(organization_id="x", conversation_id="y"))
