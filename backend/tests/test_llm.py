import json
from pathlib import Path

import httpx
import pytest

from app import main
from app.config import AppEnv, Provider, get_model_chain
from app.providers import ProviderError, _gemini_text, _openai_text, stream_provider


def event_names(chunks: list[str]) -> list[str]:
    return [chunk.splitlines()[0].removeprefix("event: ") for chunk in chunks]


def event_payload(chunk: str) -> dict[str, str]:
    return json.loads(chunk.splitlines()[1].removeprefix("data: "))


def test_model_catalog_is_hardcoded_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    test_chain = get_model_chain(AppEnv.TEST)
    production_chain = get_model_chain(AppEnv.PRODUCTION)

    assert [(item.provider, item.model) for item in test_chain] == [
        (Provider.GEMINI, "gemini-3.5-flash-lite"),
        (Provider.OPENROUTER, "minimax/minimax-m2.7"),
        (Provider.GROQ, "llama-3.3-70b-versatile"),
    ]
    assert [(item.provider, item.model) for item in production_chain] == [
        (Provider.OPENAI, "gpt-5.6-luna"),
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

    monkeypatch.setattr(main, "stream_provider", always_fails)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
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

    monkeypatch.setattr(main, "stream_provider", succeeds_on_gemini)
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

    monkeypatch.setattr(main, "stream_provider", emits_then_fails)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [Provider.GEMINI]
    assert event_names(chunks) == ["token", "error"]
    assert event_payload(chunks[0]) == {"text": "partial"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "expected_path"),
    [
        (Provider.OPENAI, "gpt-5.6-luna", "/v1/chat/completions"),
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

    base_config = get_model_chain(AppEnv.TEST)[0]
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

    monkeypatch.setattr(main, "stream_provider", permanent_gemini_failure)
    chunks = [chunk async for chunk in main._ask_stream("hello")]

    assert attempts == [
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENROUTER,
        Provider.GROQ,
        Provider.OPENROUTER,
        Provider.GROQ,
    ]
    assert Provider.GEMINI not in attempts[1:]
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
async def test_ask_validates_prompt_and_streams_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def fake_stream(config, prompt):
        yield "respuesta"

    monkeypatch.setattr(main, "stream_provider", fake_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask", json={"prompt": "hola"})
        invalid = await client.post("/ask", json={"prompt": ""})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_ask_returns_502_when_all_providers_fail_before_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def failed_stream(config, prompt):
        raise ProviderError("upstream unavailable")
        yield prompt

    monkeypatch.setattr(main, "stream_provider", failed_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask", json={"prompt": "hola"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Los providers no pudieron responder."}


@pytest.mark.asyncio
async def test_ask_returns_503_without_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask", json={"prompt": "hola"})

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ask_returns_500_for_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask", json={"prompt": "hola"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Configuración de entorno inválida"}


@pytest.mark.asyncio
async def test_partial_provider_failure_keeps_200_and_emits_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def partial_stream(config, prompt):
        yield "parcial"
        raise ProviderError("connection lost")

    monkeypatch.setattr(main, "stream_provider", partial_stream)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask", json={"prompt": "hola"})

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
