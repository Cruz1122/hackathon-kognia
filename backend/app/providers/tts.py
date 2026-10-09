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

    def synthesize_wav(self, text: str) -> bytes:
        return piper.synthesize_text(text)
