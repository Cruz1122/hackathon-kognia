from __future__ import annotations

import json

import httpx
import pytest

from app.config import AppEnv, Provider, get_model_chain
from app.features.agent.service import stream_agent
from app.features.agent.tools import CANONICAL_TOOLS
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
async def test_provider_error_from_missing_key() -> None:
    config = next(item for item in get_model_chain(AppEnv.TEST) if item.provider is Provider.OPENAI)
    config = config.__class__(config.provider, config.model, "", config.base_url)
    with pytest.raises(ProviderError, match="Missing API key"):
        async for _event in OpenAICompatibleLLM().stream(config, "hola"):
            pass
