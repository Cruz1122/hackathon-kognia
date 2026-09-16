from __future__ import annotations

import io
import os
import wave
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from threading import Lock


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

_lock = Lock()
_voice = None
_synthesis_config = None


def _model_path() -> Path:
    directory = Path(os.getenv("PIPER_MODEL_DIR", "backend/models/piper-es"))
    if not directory.is_absolute():
        directory = REPOSITORY_ROOT / directory
    voice_name = os.getenv("PIPER_TTS_VOICE", "es_MX-claude-high")
    return directory / f"{voice_name}.onnx"


def _get_tts():
    global _voice, _synthesis_config
    if _voice is not None:
        return _voice, _synthesis_config
    with _lock:
        if _voice is None:
            piper = import_module("piper")
            _voice = piper.PiperVoice.load(_model_path())
            _synthesis_config = piper.SynthesisConfig(
                noise_scale=0.0,
                noise_w_scale=0.0,
            )
    return _voice, _synthesis_config


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def preload_tts() -> None:
    """Load the local Piper voice once before accepting calls."""
    _get_tts()


def tts_sample_rate() -> int:
    voice, _ = _get_tts()
    return int(voice.config.sample_rate)


def stream_tts_audio(text: str) -> Iterator[bytes]:
    """Yield every signed-int16 PCM chunk synthesized by Piper."""
    voice, synthesis_config = _get_tts()
    with _lock:
        for chunk in voice.synthesize(text, syn_config=synthesis_config):
            yield chunk.audio_int16_bytes


def synthesize_text(text: str) -> bytes:
    """Generate a mono WAV using the resident Piper voice."""
    sample_rate = tts_sample_rate()
    return _wav_bytes(b"".join(stream_tts_audio(text)), sample_rate)
