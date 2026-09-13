from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import ModelConfig


@dataclass(frozen=True)
class LLMCapabilities:
    supports_tools: bool
    supports_streaming: bool = True


@dataclass(frozen=True)
class CanonicalTool:
    name: str
    description: str
    parameters: dict[str, Any]


class LLMProvider(Protocol):
    """Streaming chat backend. Tool JSON is canonical; adapters translate it."""

    capabilities: LLMCapabilities

    def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...


class SpeechToTextProvider(Protocol):
    def preload(self) -> None: ...

    def create_stream(self) -> Any: ...

    def reset_stream(self, stream: Any) -> None: ...

    def feed_pcm(self, stream: Any, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]: ...

    def finish_stream(self, stream: Any, sample_rate: int = 16000) -> str: ...

    def pcm_wave_level(self, pcm: bytes, sample_rate: int = 16000) -> float: ...

    def transcribe_audio(self, audio: bytes, content_type: str) -> str: ...


class TextToSpeechProvider(Protocol):
    def preload(self) -> None: ...

    def sample_rate(self) -> int: ...

    def stream_audio(self, text: str) -> Iterator[bytes]: ...

    def synthesize_wav(self, text: str) -> bytes: ...
