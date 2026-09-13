from __future__ import annotations

from typing import Any

from ..features.transcription import service as sherpa


class SherpaSpeechToText:
    """Existing Sherpa-ONNX pipeline behind the STT contract."""

    def preload(self) -> None:
        sherpa.preload_model()

    def create_stream(self) -> Any:
        return sherpa.create_stream()

    def reset_stream(self, stream: Any) -> None:
        sherpa.reset_stream(stream)

    def feed_pcm(self, stream: Any, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
        return sherpa.feed_pcm(stream, pcm, sample_rate)

    def finish_stream(self, stream: Any, sample_rate: int = 16000) -> str:
        return sherpa.finish_stream(stream, sample_rate)

    def pcm_wave_level(self, pcm: bytes, sample_rate: int = 16000) -> float:
        return sherpa.pcm_wave_level(pcm, sample_rate)

    def transcribe_audio(self, audio: bytes, content_type: str) -> str:
        return sherpa.transcribe_audio(audio, content_type)
