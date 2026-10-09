from __future__ import annotations

from collections.abc import Iterator

from ..features.synthesis import service as voice
from ..features.synthesis.service import ElevenLabsTurn


class ElevenLabsTTSProvider:
    """Telephony wrapper around the shared ElevenLabs turn."""

    def preload(self) -> None:
        voice.preload_tts()

    def sample_rate(self) -> int:
        return int(voice.tts_sample_rate())

    def audio_format(self) -> str:
        return "pcm_s16le"

    def open_turn(self) -> ElevenLabsTurn:
        return voice.ElevenLabsTurn()

    def stream_audio(self, text: str) -> Iterator[bytes]:
        yield from voice.stream_tts_audio(text)

    def preload_phrases(self, texts: list[str] | tuple[str, ...]) -> None:
        voice.preload_tts_phrases(texts)

    def cached_audio(self, text: str) -> tuple[int, bytes] | None:
        return voice.cached_tts_audio(text)

    def close(self) -> None:
        return None
