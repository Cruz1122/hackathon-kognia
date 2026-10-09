from __future__ import annotations

from collections.abc import Iterator

from ..features.synthesis import service as voice


class ElevenLabsTextToSpeech:
    """ElevenLabs streaming voice behind the TTS contract."""

    def preload(self) -> None:
        voice.preload_tts()

    def sample_rate(self) -> int:
        return voice.tts_sample_rate()

    def open_turn(self) -> voice.ElevenLabsTurn:
        return voice.ElevenLabsTurn()

    def stream_audio(self, text: str) -> Iterator[bytes]:
        return voice.stream_tts_audio(text)

    def preload_phrases(self, texts: list[str] | tuple[str, ...]) -> None:
        voice.preload_tts_phrases(texts)

    def cached_audio(self, text: str) -> tuple[int, bytes] | None:
        return voice.cached_tts_audio(text)

    def synthesize_wav(self, text: str) -> bytes:
        return voice.synthesize_text(text)
