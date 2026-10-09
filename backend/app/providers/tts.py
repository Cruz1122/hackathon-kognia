from __future__ import annotations

from collections.abc import Iterator

from ..features.synthesis import service as piper


class PiperTextToSpeech:
    """Existing Piper voice behind the TTS contract."""

    def preload(self) -> None:
        piper.preload_tts()

    def sample_rate(self) -> int:
        return piper.tts_sample_rate()

    def stream_audio(self, text: str) -> Iterator[bytes]:
        return piper.stream_tts_audio(text)

    def preload_phrases(self, texts: list[str] | tuple[str, ...]) -> None:
        piper.preload_tts_phrases(texts)

    def cached_audio(self, text: str) -> tuple[int, bytes] | None:
        return piper.cached_tts_audio(text)

    def synthesize_wav(self, text: str) -> bytes:
        return piper.synthesize_text(text)
