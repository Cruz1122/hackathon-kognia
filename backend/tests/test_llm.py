import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.auth.tokens import create_access_token
from app.config import AppEnv, Provider, get_model_chain
from app.db.models import User, UserRole
from app.db.session import get_db
from app.features.agent.tools import CANONICAL_TOOLS
from app.providers import FakeLLM, FakeSTT, FakeTTS, ProviderError, _gemini_text, _openai_text, stream_chat, stream_provider


@pytest.fixture
def auth_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    secret = "test-llm-jwt-secret-012345678901234567890"
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    user = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="admin@test.invalid",
        password_hash="hash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session = AsyncMock()
    session.get.return_value = user

    async def override_db():
        yield session

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_db)
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def event_names(chunks: list[str]) -> list[str]:
    return [chunk.splitlines()[0].removeprefix("event: ") for chunk in chunks]


def event_payload(chunk: str) -> dict[str, str]:
    return json.loads(chunk.splitlines()[1].removeprefix("data: "))


def install_token_llm(monkeypatch: pytest.MonkeyPatch, handler) -> FakeLLM:
    async def stream(config, prompt, *, messages=None, tools=None):
        del messages, tools
        async for item in handler(config, prompt):
            yield item

    fake = FakeLLM(stream, supports_tools=False)
    monkeypatch.setattr(main, "llm_provider", fake)
    return fake


def install_message_llm(monkeypatch: pytest.MonkeyPatch, handler) -> FakeLLM:
    async def stream(config, prompt, *, messages=None, tools=None):
        del tools
        async for item in handler(config, prompt, messages=messages):
            yield item

    fake = FakeLLM(stream, supports_tools=False)
    monkeypatch.setattr(main, "llm_provider", fake)
    return fake


def test_model_catalog_is_hardcoded_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    test_chain = get_model_chain(AppEnv.TEST)
    production_chain = get_model_chain(AppEnv.PRODUCTION)

    assert [(item.provider, item.model) for item in test_chain] == [
        (Provider.OPENAI, "gpt-5.4-mini"),
        (Provider.GEMINI, "gemini-3.5-flash-lite"),
        (Provider.OPENROUTER, "minimax/minimax-m2.7"),
        (Provider.GROQ, "llama-3.3-70b-versatile"),
    ]
    assert [(item.provider, item.model) for item in production_chain] == [
        (Provider.OPENAI, "gpt-6-luna"),
        (Provider.GEMINI, "gemini-3.5-flash-lite"),
    ]


@pytest.mark.asyncio
async def test_test_environment_fallback_is_circular_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    attempts: list[Provider] = []

    async def always_fails(config, prompt):
        attempts.append(config.provider)
        raise ProviderError("upstream unavailable")
        yield prompt

    install_token_llm(monkeypatch, always_fails)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [
        Provider.OPENAI,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENAI,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENAI,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
    ]
    assert event_names(chunks) == ["error"]


@pytest.mark.asyncio
async def test_production_falls_back_from_luna_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    attempts: list[Provider] = []

    async def succeeds_on_gemini(config, prompt):
        attempts.append(config.provider)
        if config.provider is Provider.OPENAI:
            raise ProviderError("luna unavailable")
        yield "hola"

    install_token_llm(monkeypatch, succeeds_on_gemini)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [Provider.OPENAI, Provider.OPENAI, Provider.OPENAI, Provider.GEMINI]
    assert event_names(chunks) == ["token", "done"]
    assert event_payload(chunks[0]) == {"text": "hola"}


@pytest.mark.asyncio
async def test_partial_stream_does_not_fallback_or_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    attempts: list[Provider] = []

    async def emits_then_fails(config, prompt):
        attempts.append(config.provider)
        yield "partial"
        raise ProviderError("connection lost")

    install_token_llm(monkeypatch, emits_then_fails)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [Provider.OPENAI]
    assert event_names(chunks) == ["token", "error"]
    assert event_payload(chunks[0]) == {"text": "partial"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "expected_path"),
    [
        (Provider.OPENAI, "gpt-6-luna", "/v1/chat/completions"),
        (Provider.OPENROUTER, "minimax/minimax-m2.7", "/api/v1/chat/completions"),
        (Provider.GROQ, "llama-3.3-70b-versatile", "/openai/v1/chat/completions"),
    ],
)
async def test_openai_compatible_providers_stream_tokens(
    provider: Provider, model: str, expected_path: str
) -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = (
            'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    source_chain = get_model_chain(AppEnv.PRODUCTION if provider is Provider.OPENAI else AppEnv.TEST)
    config = next(item for item in source_chain if item.provider is provider)
    config = config.__class__(provider, model, "secret", config.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tokens = [token async for token in stream_provider(config, "hello", client)]

    assert captured is not None
    assert captured.url.path == expected_path
    assert captured.headers["authorization"] == "Bearer secret"
    assert json.loads(captured.content)["stream"] is True
    assert tokens == ["A", "B"]


@pytest.mark.asyncio
async def test_reasoning_model_retries_tool_call_with_reasoning_effort_none() -> None:
    # Regression: reasoning models (gpt-5.6-luna / gpt-6-luna) reject function
    # tools on /chat/completions with HTTP 400 unless reasoning_effort is "none".
    # The provider must retry once with that parameter and still surface the
    # tool calls, instead of treating the request as a permanent provider error.
    requests: list[httpx.Request] = []
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {
                                "name": "sum_numbers",
                                "arguments": json.dumps({"numbers": [3, 4]}),
                            },
                        }
                    ]
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if body.get("tools") and body.get("reasoning_effort") != "none":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Function tools with reasoning_effort are not supported for "
                            "gpt-6-luna in /v1/chat/completions. To use function tools, "
                            "use /v1/responses or set reasoning_effort to 'none'."
                        )
                    }
                },
            )
        content = f"data: {json.dumps(tool_call_chunk)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    base = next(
        item for item in get_model_chain(AppEnv.PRODUCTION) if item.provider is Provider.OPENAI
    )
    config = base.__class__(base.provider, "gpt-6-luna", "secret", base.base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in stream_chat(config, "suma 3 y 4", client, tools=CANONICAL_TOOLS)
        ]

    assert len(requests) == 2
    assert json.loads(requests[0].content).get("reasoning_effort") is None
    assert json.loads(requests[1].content)["reasoning_effort"] == "none"
    assert events == [
        (
            "tool_calls",
            {"calls": [{"id": "call_1", "name": "sum_numbers", "arguments": '{"numbers": [3, 4]}'}]},
        )
    ]


@pytest.mark.asyncio
async def test_gemini_stream_uses_header_auth_and_native_payload() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        body = (
            'data: {"candidates":[{"content":{"parts":[{"text":"Hola"}]}}]}\n\n'
            "data: {}\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    base_config = next(
        item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.GEMINI
    )
    config = base_config.__class__(
        base_config.provider, base_config.model, "secret", base_config.base_url
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tokens = [token async for token in stream_provider(config, "hello", client)]

    assert captured is not None
    assert captured.url.path.endswith("/models/gemini-3.5-flash-lite:streamGenerateContent")
    assert captured.headers["x-goog-api-key"] == "secret"
    assert "key" not in captured.url.query.decode()
    assert tokens == ["Hola"]


@pytest.mark.asyncio
async def test_permanent_failure_skips_provider_for_remaining_circular_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    attempts: list[Provider] = []

    async def permanent_gemini_failure(config, prompt):
        attempts.append(config.provider)
        if config.provider is Provider.GEMINI:
            raise ProviderError("unauthorized", retryable=False)
        raise ProviderError("temporary failure")
        yield prompt

    install_token_llm(monkeypatch, permanent_gemini_failure)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [
        Provider.OPENAI,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENAI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENAI,
        Provider.OPENROUTER,
        Provider.GROQ,
    ]
    assert attempts.count(Provider.GEMINI) == 1
    assert event_names(chunks) == ["error"]


@pytest.mark.parametrize("payload", [[], {"choices": [{"delta": []}]}])
def test_openai_parser_rejects_malformed_payloads(payload) -> None:
    with pytest.raises(ProviderError):
        _openai_text(payload)


@pytest.mark.parametrize("payload", [[], {"candidates": ["invalid"]}])
def test_gemini_parser_rejects_malformed_payloads(payload) -> None:
    with pytest.raises(ProviderError):
        _gemini_text(payload)


@pytest.mark.asyncio
async def test_ask_validates_prompt_and_streams_sse(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def fake_stream(config, prompt, *, messages):
        yield "respuesta"

    install_message_llm(monkeypatch, fake_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "hola", "messages": [{"role": "user", "content": "contexto"}]},
        )
        invalid = await client.post("/ask", headers=auth_headers, json={"prompt": ""})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert invalid.status_code == 422


def test_context_window_keeps_the_whole_conversation() -> None:
    from app.features.agent.service import context_window

    history = [
        {"role": "user", "content": f"u{index}"} if index % 2 == 0 else {"role": "assistant", "content": f"a{index}"}
        for index in range(20)
    ]
    assert context_window(history) == history


def test_context_window_merges_cut_user_speech() -> None:
    from app.features.agent.service import context_window

    # A slow speaker cut by the turn-taking logic leaves two user messages in a
    # row; the window must present them as a single request, not as a new one.
    history = [
        {"role": "assistant", "content": "¿En qué puedo ayudarte?"},
        {"role": "user", "content": "quiero cancelar..."},
        {"role": "user", "content": "reserva."},
    ]
    windowed = context_window(history)

    assert windowed == [
        {"role": "assistant", "content": "¿En qué puedo ayudarte?"},
        {"role": "user", "content": "quiero cancelar... reserva."},
    ]


def test_merge_consecutive_user_messages_keeps_role_boundaries() -> None:
    from app.features.agent.service import merge_consecutive_user_messages

    merged = merge_consecutive_user_messages([
        {"role": "user", "content": "uno"},
        {"role": "user", "content": "dos"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "tres"},
        {"role": "user", "content": "cuatro"},
        {"role": "user", "content": "cinco"},
    ])

    assert merged == [
        {"role": "user", "content": "uno dos"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "tres cuatro cinco"},
    ]


def test_context_window_keeps_consecutive_user_fragments_with_their_turn() -> None:
    from app.features.agent.service import context_window

    history: list[dict[str, str]] = [
        {"role": "system", "content": "reglas"},
        {"role": "user", "content": "t1"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "t2"},
        {"role": "assistant", "content": "r2"},
        {"role": "user", "content": "t3"},
        {"role": "assistant", "content": "r3"},
        {"role": "user", "content": "t4"},
        {"role": "assistant", "content": "r4"},
        {"role": "user", "content": "t5"},
        {"role": "assistant", "content": "r5"},
        {"role": "user", "content": "t6"},
        {"role": "assistant", "content": "r6"},
    ]
    windowed = context_window(history)

    assert windowed == [
        {"role": "user", "content": "t1"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "t2"},
        {"role": "assistant", "content": "r2"},
        {"role": "user", "content": "t3"},
        {"role": "assistant", "content": "r3"},
        {"role": "user", "content": "t4"},
        {"role": "assistant", "content": "r4"},
        {"role": "user", "content": "t5"},
        {"role": "assistant", "content": "r5"},
        {"role": "user", "content": "t6"},
        {"role": "assistant", "content": "r6"},
    ]
    assert all(item["role"] in {"user", "assistant"} for item in windowed)


def test_context_window_preserves_fragmented_speech_turn() -> None:
    from app.features.agent.service import context_window

    history: list[dict[str, str]] = [
        {"role": "user", "content": "Listo, me gustaría ver si tienes acceso"},
        {"role": "assistant", "content": "¿A qué te refieres exactamente?"},
        {"role": "user", "content": "reservas y sumas"},
        {"role": "assistant", "content": "Puedo con reservas y sumas."},
    ]
    assert context_window(history) == history


def test_context_window_keeps_opening_and_later_turns() -> None:
    from app.features.agent.service import context_window

    opening = [
        {"role": "user", "content": "¿Cómo puedo hacer una reserva?"},
        {"role": "assistant", "content": "Dime fecha y hora."},
    ]
    later = [
        {"role": "user", "content": f"u{index}"} if index % 2 == 0 else {"role": "assistant", "content": f"a{index}"}
        for index in range(20)
    ]
    assert context_window([*opening, *later]) == [*opening, *later]


def test_agent_system_excludes_instructions_from_the_call() -> None:
    from app.features.agent.tools import AGENT_SYSTEM

    normalized = AGENT_SYSTEM.casefold()
    assert "no forman parte de la llamada" in normalized
    assert "primer mensaje del usuario" in normalized


@pytest.mark.asyncio
async def test_ask_sends_the_whole_conversation(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    captured: list[dict[str, str]] = []

    async def fake_stream(config, prompt, *, messages):
        captured.extend(messages)
        yield "respuesta"

    install_message_llm(monkeypatch, fake_stream)
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
        for index in range(24)
    ]
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "¿Y ahora?", "messages": history, "channel": "voice-demo"},
        )

    assert response.status_code == 200
    assert captured == [*history, {"role": "user", "content": "¿Y ahora?"}]


@pytest.mark.asyncio
async def test_ask_merges_prompt_with_cut_user_fragment(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """A prompt that continues a cut-off user fragment reaches the model as one message."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    captured: list[dict[str, str]] = []

    async def fake_stream(config, prompt, *, messages):
        captured.extend(messages)
        yield "respuesta"

    install_message_llm(monkeypatch, fake_stream)
    history = [
        {"role": "assistant", "content": "¿En qué puedo ayudarte?"},
        {"role": "user", "content": "quiero cancelar..."},
    ]
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "reserva.", "messages": history, "channel": "voice-demo"},
        )

    assert response.status_code == 200
    assert captured == [
        {"role": "assistant", "content": "¿En qué puedo ayudarte?"},
        {"role": "user", "content": "quiero cancelar... reserva."},
    ]


@pytest.mark.asyncio
async def test_ask_forwards_voice_history_to_the_shared_agent(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    captured: list[dict[str, str]] = []

    async def fake_stream(config, prompt, *, messages):
        captured.extend(messages)
        yield "respuesta"

    fake = install_message_llm(monkeypatch, fake_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={
                "prompt": "¿Y después?",
                "messages": [{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "Hola, ¿cómo estás?"}],
                "channel": "voice-demo",
            },
        )

    assert response.status_code == 200
    assert captured == [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Hola, ¿cómo estás?"},
        {"role": "user", "content": "¿Y después?"},
    ]


@pytest.mark.asyncio
async def test_transcribe_accepts_raw_browser_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSTT("transcripción local")
    monkeypatch.setattr(main, "stt_provider", fake)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/transcribe",
            content=b"browser-audio",
            headers={"Content-Type": "audio/webm;codecs=opus"},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "transcripción local"}
    assert fake.transcripts == [(b"browser-audio", "audio/webm;codecs=opus")]


@pytest.mark.asyncio
async def test_synthesize_returns_local_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "tts_provider", FakeTTS())
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/synthesize", json={"text": "Hola"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-fake-wav"


@pytest.mark.asyncio
async def test_ask_returns_502_when_all_providers_fail_before_stream(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def failed_stream(config, prompt, *, messages):
        raise ProviderError("upstream unavailable")
        yield prompt

    install_message_llm(monkeypatch, failed_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "hola", "messages": [{"role": "user", "content": "contexto"}]},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Los providers no pudieron responder."}


@pytest.mark.asyncio
async def test_ask_returns_503_without_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "hola", "messages": [{"role": "user", "content": "contexto"}]},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ask_returns_500_for_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "hola", "messages": [{"role": "user", "content": "contexto"}]},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Configuración de entorno inválida"}


@pytest.mark.asyncio
async def test_partial_provider_failure_keeps_200_and_emits_error(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def partial_stream(config, prompt, *, messages):
        yield "parcial"
        raise ProviderError("connection lost")

    install_message_llm(monkeypatch, partial_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=auth_headers,
            json={"prompt": "hola", "messages": [{"role": "user", "content": "contexto"}]},
        )

    assert response.status_code == 200
    assert "event: token" in response.text
    assert "event: error" in response.text


@pytest.mark.asyncio
async def test_ask_is_documented_in_openapi() -> None:
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    operation = response.json()["paths"]["/ask"]["post"]
    assert operation["summary"] == "Pregunta a los modelos configurados"
    assert "200" in operation["responses"]
    assert "422" in operation["responses"]
    assert "502" in operation["responses"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]


def test_loads_environment_from_repository_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KOGNIA_ENV_LOAD_TEST=loaded\n", encoding="utf-8")
    monkeypatch.delenv("KOGNIA_ENV_LOAD_TEST", raising=False)

    main.load_repository_environment(env_file)

    assert main.os.getenv("KOGNIA_ENV_LOAD_TEST") == "loaded"


def test_demo_knowledge_corpus_is_packaged() -> None:
    path = main.demo_knowledge_path()
    assert path is not None
    assert "POL-R48329" in path.read_text(encoding="utf-8")
