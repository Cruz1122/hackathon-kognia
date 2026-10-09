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

    def close(self) -> None:
        return None
