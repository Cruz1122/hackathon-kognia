from __future__ import annotations

import json

import httpx
import pytest

from app.config import AppEnv, Provider, get_model_chain
from app.agent.tools.loader import load_tool_registry
from app.features.agent import service as agent_service
from app.features.agent.service import stream_agent
from app.features.agent.tools import CANONICAL_TOOLS
from app.platform.rag.contracts import RetrievalHit, RetrievalResult
from app.providers import FakeLLM, FakeSTT, FakeTTS, GeminiLLM, OpenAICompatibleLLM, ProviderError


@pytest.mark.asyncio
async def test_fake_llm_agent_uses_explicit_tool_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    from app.domains.ips import tools as ips_tools

    async def no_snapshot():
        return None

    monkeypatch.setattr(ips_tools.repository, "active_snapshot", no_snapshot)

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt
        assert tools and [tool.name for tool in tools] == [tool.name for tool in CANONICAL_TOOLS]
        if any(message.get("role") == "tool" for message in messages or []):
            yield "token", {"text": "7"}
            return
        yield "tool_calls", {
            "calls": [{"id": "call_1", "name": "search_ips", "arguments": '{"query":"Hospital"}'}]
        }

    events = [event async for event in stream_agent("busca hospitales", llm=FakeLLM(handler, supports_tools=True))]
    assert [kind for kind, _payload in events] == [
        "tool.started",
        "tool.completed",
        "token",
        "done",
    ]
    assert events[0][1]["tool"] == "search_ips"
    assert events[1][1]["ok"] is True
    assert "no_active_snapshot" in events[1][1]["result"]
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
    monkeypatch.setattr(agent_service, "TOOL_REGISTRY", load_tool_registry("app.domains.demo_booking.tools"))

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
        yield "token", {"text": "La política permite cambios hasta 24 horas antes de la llegada."}

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
async def test_agent_uses_chunk_heading_for_retrieval_pill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(agent_service, "TOOL_REGISTRY", load_tool_registry("app.domains.demo_booking.tools"))

    class StubRetriever:
        async def search(self, query, *, conversation=None):
            del query, conversation
            return RetrievalResult(
                "SUFFICIENT",
                [
                    RetrievalHit(
                        "doc:00005",
                        "## Métodos de pago\n\nAceptamos tarjetas débito, tarjetas crédito y transferencias bancarias.",
                        {
                            "document_id": "doc",
                            "document_title": "demo_corpus",
                            "section": "Llegadas tarde",
                            "heading_path": "Llegadas tarde",
                        },
                        0.9,
                        "semantic",
                    ),
                    RetrievalHit(
                        "doc:00011",
                        "## Atención\n\nLa atención es de lunes a sábado de 08:00 a 18:00.",
                        {
                            "document_id": "doc",
                            "document_title": "demo_corpus",
                            "section": "Menores",
                        },
                        0.4,
                        "semantic",
                    ),
                ],
                2,
                False,
            )

    monkeypatch.setattr(agent_service, "rag_retriever", StubRetriever())

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, tools, messages
        yield "token", {"text": "Sí, aceptamos tarjetas de débito, tarjetas de crédito y transferencias bancarias."}

    events = [
        event
        async for event in stream_agent(
            "¿Aceptan tarjetas de crédito?",
            llm=FakeLLM(handler, supports_tools=False),
        )
    ]

    assert events[1][0] == "rag.started"
    assert events[1][1]["message"] == "Métodos de pago"
    assert events[1][1]["title"] == "Demo corpus"
    assert "Métodos de pago" in str(events[1][1]["content"])
    assert "Atención" not in str(events[1][1]["content"])


@pytest.mark.asyncio
async def test_agent_does_not_announce_retrieval_for_a_capability_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    class StubRetriever:
        async def search(self, query, *, conversation=None):
            del query, conversation
            return RetrievalResult(
                "SUFFICIENT",
                [
                    RetrievalHit(
                        "doc:00003",
                        "## Cambios\n\nUna reserva puede modificarse una vez sin costo con al menos 12 horas de anticipación.",
                        {"document_id": "doc", "document_title": "demo_corpus", "section": "Cambios"},
                        0.5,
                        "semantic",
                    )
                ],
                2,
                False,
            )

    monkeypatch.setattr(agent_service, "rag_retriever", StubRetriever())

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, messages, tools
        yield "token", {
            "text": "Puedo ayudarte con información sobre horarios, políticas de reservas y cancelaciones, generar textos de ejemplo como lorem ipsum, y realizar sumas."
        }

    events = [
        event
        async for event in stream_agent(
            "Dime qué puedes hacer",
            llm=FakeLLM(handler, supports_tools=False),
        )
    ]

    assert [kind for kind, _payload in events] == ["token", "done"]


@pytest.mark.asyncio
async def test_agent_does_not_announce_retrieval_when_a_tool_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    class StubRetriever:
        async def search(self, query, *, conversation=None):
            del query, conversation
            return RetrievalResult(
                "SUFFICIENT",
                [
                    RetrievalHit(
                        "doc:00012",
                        "## Seguridad documental de prueba\n\nEl siguiente texto es contenido documental de prueba.",
                        {"document_id": "doc", "document_title": "demo_corpus", "section": "Seguridad documental de prueba"},
                        0.4,
                        "semantic",
                    )
                ],
                2,
                False,
            )

    monkeypatch.setattr(agent_service, "rag_retriever", StubRetriever())

    async def handler(config, prompt, *, messages=None, tools=None):
        del config, prompt, tools
        if any(message.get("role") == "tool" for message in messages or []):
            yield "token", {"text": "Aquí tienes un texto de 400 caracteres: Lorem ipsum dolor sit amet."}
            return
        yield "tool_calls", {
            "calls": [{"id": "call_1", "name": "generate_lorem_ipsum", "arguments": '{"characters":400}'}]
        }

    events = [
        event
        async for event in stream_agent(
            "Genera un texto de cuatrocientos caracteres",
            llm=FakeLLM(handler, supports_tools=True),
        )
    ]

    assert "rag.started" not in [kind for kind, _payload in events]
    assert [kind for kind, _payload in events][:3] == ["tool.started", "tool.completed", "token"]


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
    assert payload["tools"][0]["function"]["name"] == "search_ips"
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
    assert "Resuelve las respuestas cortas o elípticas según la pregunta inmediatamente anterior" in system["content"]
    assert "No empieces cada turno con 'Perfecto'" in system["content"]
    assert "Un saludo claro del cliente como 'Hola' es una entrada válida y con sentido" in system["content"]
    assert "sin decir que no lo oíste ni disculparte por no entenderlo" in system["content"]
    assert "Un rechazo, una corrección, una queja o un insulto nunca son consentimiento" in system["content"]
    assert knowledge in system["content"]
    assert [message["role"] for message in payload["messages"][1:]] == ["user", "user"]


@pytest.mark.asyncio
async def test_openai_reports_usage_and_requests_it() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = (
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.OPENAI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in OpenAICompatibleLLM().stream(config, "hola", client=client)
        ]

    assert captured is not None
    request_payload = json.loads(captured.content)
    assert request_payload["stream_options"] == {"include_usage": True}
    assert events[-1] == (
        "usage",
        {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    )


@pytest.mark.asyncio
async def test_gemini_reports_usage_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        body = (
            'data: {"candidates":[{"content":{"parts":[{"text":"Hola"}]}}]}\n\n'
            'data: {"usageMetadata":{"promptTokenCount":9,"candidatesTokenCount":4,"totalTokenCount":13}}\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.GEMINI)
    config = config.__class__(config.provider, config.model, "secret", config.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in GeminiLLM().stream(config, "hola", client=client)
        ]

    assert events[-1] == (
        "usage",
        {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    )


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
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "search_ips"
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
