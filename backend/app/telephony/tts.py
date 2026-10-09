from __future__ import annotations

from collections.abc import Iterator

from ..features.synthesis import service as piper


class PiperTTSProvider:
    """Piper wrapper that keeps the existing synthesis lock."""

    def preload(self) -> None:
        piper.preload_tts()

    def sample_rate(self) -> int:
        return int(piper.tts_sample_rate())

    def audio_format(self) -> str:
        return "pcm_s16le"

    def stream_audio(self, text: str) -> Iterator[bytes]:
        yield from piper.stream_tts_audio(text)

    def preload_phrases(self, texts: list[str] | tuple[str, ...]) -> None:
        piper.preload_tts_phrases(texts)

    def cached_audio(self, text: str) -> tuple[int, bytes] | None:
        return piper.cached_tts_audio(text)

    def close(self) -> None:
        return None
