from __future__ import annotations

import json

import httpx
import pytest

from app.config import AppEnv, Provider, get_model_chain
from app.features.agent import service as agent_service
from app.features.agent.service import stream_agent
from app.features.agent.tools import CANONICAL_TOOLS
from app.platform.rag.contracts import RetrievalHit, RetrievalResult
from app.providers import FakeLLM, FakeSTT, FakeTTS, GeminiLLM, OpenAICompatibleLLM, ProviderError


@pytest.mark.asyncio
async def test_fake_llm_agent_uses_explicit_tool_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt
        assert tools and [tool.name for tool in tools] == [tool.name for tool in CANONICAL_TOOLS]
        if any(message.get("role") == "tool" for message in messages or []):
            yield "token", {"text": "7"}
            return
        yield "tool_calls", {
            "calls": [{"id": "call_1", "name": "sum_numbers", "arguments": '{"numbers":[3,4]}'}]
        }

    events = [event async for event in stream_agent("suma 3 y 4", llm=FakeLLM(handler, supports_tools=True))]
    assert [kind for kind, _payload in events] == [
        "tool.started",
        "tool.completed",
        "token",
        "done",
    ]
    assert events[0][1]["title"] == "Suma de números"
    assert events[0][1]["inputs"] == [{"label": "Números", "value": "3, 4"}]
    assert events[1][1]["outputs"] == [{"label": "Total", "value": "7"}]
    assert events[2][1]["text"] == "7"


@pytest.mark.asyncio
async def test_fake_llm_without_tools_skips_tool_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, messages
        assert tools is None
        yield "token", {"text": "hola"}

    events = [event async for event in stream_agent("hola", llm=FakeLLM(handler, supports_tools=False))]
    assert [kind for kind, _payload in events] == ["token", "done"]


@pytest.mark.asyncio
async def test_agent_emits_one_retrieval_event_pair_when_context_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    class StubRetriever:
        calls = 0

        async def search(self, query, *, conversation=None):
            self.calls += 1
            assert query == "¿Cuál es la política?"
            assert conversation == []
            return RetrievalResult(
                "SUFFICIENT",
                [
                    RetrievalHit(
                        "doc:00001",
                        "La política permite cambios hasta 24 horas antes de la llegada.",
                        {
                            "document_id": "doc",
                            "document_title": "Políticas de reservas",
                            "source_filename": "politicas.md",
                            "section": "Políticas de reembolso",
                            "line_start": 12,
                            "line_end": 15,
                            "source_type": "md",
                        },
                        0.1234,
                        "semantic",
                    )
                ],
                2,
                False,
            )

    retriever = StubRetriever()
    monkeypatch.setattr(agent_service, "rag_retriever", retriever)

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, tools
        assert messages and messages[0]["role"] == "system"
        assert "knowledge_status=available" in str(messages[0]["content"])
        yield "token", {"text": "Claro. Encontré la política."}

    events = [
        event
        async for event in stream_agent(
            "¿Cuál es la política?",
            llm=FakeLLM(handler, supports_tools=False),
        )
    ]

    assert retriever.calls == 1
    assert [kind for kind, _payload in events] == ["token", "rag.started", "rag.completed", "done"]
    assert events[1][1] == {
        "used_rag": True,
        "message": "Políticas de reembolso",
        "title": "Políticas de reservas",
        "content": "La política permite cambios hasta 24 horas antes de la llegada.",
    }
    assert events[2][1] == events[1][1]


@pytest.mark.asyncio
async def test_agent_does_not_announce_retrieval_for_a_clarification_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    class StubRetriever:
        async def search(self, query, *, conversation=None):
            del query, conversation
            return RetrievalResult(
                "SUFFICIENT",
                [
                    RetrievalHit(
                        "doc:00001",
                        "La política permite cambios hasta 24 horas antes de la llegada.",
                        {"document_id": "doc", "source_filename": "politicas.md"},
                        0.1234,
                        "semantic",
                    )
                ],
                2,
                False,
            )

    monkeypatch.setattr(agent_service, "rag_retriever", StubRetriever())

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, messages, tools
        yield "token", {"text": "Parece que tu mensaje se cortó. ¿Puedes decirme qué necesitas?"}

    events = [
        event
        async for event in stream_agent(
            "¿Cuál es la política?",
            llm=FakeLLM(handler, supports_tools=False),
        )
    ]

    assert [kind for kind, _payload in events] == ["token", "done"]


def test_fake_stt_and_tts_cover_the_contracts() -> None:
    stt = FakeSTT("frase")
    tts = FakeTTS(b"pcm")
    stt.preload()
    tts.preload()
    stream = stt.create_stream()
    partial, ended = stt.feed_pcm(stream, b"\x00\x10")
    assert stt.preloaded is True
    assert tts.preloaded is True
    assert (partial, ended) == ("frase", True)
    assert stt.finish_stream(stream) == "frase"
    assert stt.transcribe_audio(b"webm", "audio/webm") == "frase"
    assert tts.sample_rate() == 22050
    assert list(tts.stream_audio("Hola")) == [b"pcm"]
    assert tts.synthesize_wav("Hola") == b"RIFF-fake-wav"


@pytest.mark.asyncio
async def test_openai_adapter_translates_canonical_tools() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = (
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.OPENAI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tokens = [
            event
            async for event in OpenAICompatibleLLM().stream(
                config, "hola", tools=CANONICAL_TOOLS, client=client
            )
        ]

    assert captured is not None
    payload = json.loads(captured.content)
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["name"] == "generate_lorem_ipsum"
    assert tokens[0] == ("token", {"text": "ok"})


@pytest.mark.asyncio
async def test_openai_merges_knowledge_into_the_system_prompt() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = (
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.OPENAI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    knowledge = "knowledge_status=available\ncontent:\nLa política permite cambios."
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for _event in OpenAICompatibleLLM().stream(
            config,
            "¿Cuál es la política?",
            messages=[
                {"role": "system", "content": knowledge},
                {"role": "user", "content": "hola"},
                {"role": "user", "content": "¿Cuál es la política?"},
            ],
            client=client,
        ):
            pass

    assert captured is not None
    payload = json.loads(captured.content)
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert "agente de voz" in system["content"]
    assert knowledge in system["content"]
    assert [message["role"] for message in payload["messages"][1:]] == ["user", "user"]


@pytest.mark.asyncio
async def test_gemini_adapter_translates_canonical_tools() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = 'data: {"candidates":[{"content":{"parts":[{"text":"Hola"}]}}]}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.GEMINI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in GeminiLLM().stream(config, "hola", tools=CANONICAL_TOOLS, client=client)
        ]

    assert captured is not None
    payload = json.loads(captured.content)
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "generate_lorem_ipsum"
    assert events == [("token", {"text": "Hola"})]


@pytest.mark.asyncio
async def test_gemini_puts_knowledge_in_system_instruction() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = 'data: {"candidates":[{"content":{"parts":[{"text":"Hola"}]}}]}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.GEMINI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    knowledge = "knowledge_status=available\ncontent:\nLa política permite cambios."
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for _event in GeminiLLM().stream(
            config,
            "¿Cuál es la política?",
            messages=[
                {"role": "system", "content": knowledge},
                {"role": "user", "content": "¿Cuál es la política?"},
            ],
            client=client,
        ):
            pass

    assert captured is not None
    payload = json.loads(captured.content)
    system_text = payload["systemInstruction"]["parts"][0]["text"]
    assert "agente de voz" in system_text
    assert knowledge in system_text
    assert payload["contents"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_provider_error_from_missing_key() -> None:
    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.OPENAI)
    config = config.__class__(config.provider, config.model, "", config.base_url)
    with pytest.raises(ProviderError, match="Missing API key"):
        async for _event in OpenAICompatibleLLM().stream(config, "hola"):
            pass
