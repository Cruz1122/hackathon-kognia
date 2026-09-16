from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any

from ..config import ModelConfig
from .contracts import CanonicalTool, LLMCapabilities
from .errors import ProviderError


class FakeLLM:
    """In-memory LLM used by tests; capabilities are explicit."""

    def __init__(
        self,
        handler: Callable[..., AsyncIterator[tuple[str, dict[str, Any]] | str]],
        *,
        supports_tools: bool = False,
    ) -> None:
        self.capabilities = LLMCapabilities(supports_tools=supports_tools, supports_streaming=True)
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        del client
        self.calls.append(
            {
                "provider": config.provider,
                "model": config.model,
                "prompt": prompt,
                "messages": list(messages or []),
                "tools": [tool.name for tool in tools] if tools else [],
            }
        )
        async for item in self._handler(config, prompt, messages=messages, tools=tools):
            if isinstance(item, str):
                yield "token", {"text": item}
            else:
                yield item


class FakeSTT:
    def __init__(self, text: str = "transcripción local") -> None:
        self.text = text
        self.preloaded = False
        self.transcripts: list[tuple[bytes, str]] = []

    def preload(self) -> None:
        self.preloaded = True

    def create_stream(self) -> dict[str, str]:
        return {"kind": "fake-stt"}

    def reset_stream(self, stream: Any) -> None:
        del stream

    def feed_pcm(self, stream: Any, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
        del stream, sample_rate
        return (self.text if pcm else "", bool(pcm))

    def finish_stream(self, stream: Any, sample_rate: int = 16000) -> str:
        del stream, sample_rate
        return self.text

    def pcm_wave_level(self, pcm: bytes, sample_rate: int = 16000) -> float:
        del pcm, sample_rate
        return 0.2

    def transcribe_audio(self, audio: bytes, content_type: str) -> str:
        self.transcripts.append((audio, content_type))
        return self.text


class FakeTTS:
    def __init__(self, chunk: bytes = b"pcm-fake") -> None:
        self.chunk = chunk
        self.preloaded = False
        self.spoken: list[str] = []

    def preload(self) -> None:
        self.preloaded = True

    def sample_rate(self) -> int:
        return 22050

    def stream_audio(self, text: str) -> Iterator[bytes]:
        self.spoken.append(text)
        yield self.chunk

    def synthesize_wav(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"RIFF-fake-wav"


class FailingLLM:
    capabilities = LLMCapabilities(supports_tools=False, supports_streaming=True)

    def __init__(self, *, retryable: bool = True) -> None:
        self.retryable = retryable

    async def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        del config, prompt, messages, tools, client
        raise ProviderError("upstream unavailable", retryable=self.retryable)
        yield "token", {"text": ""}
