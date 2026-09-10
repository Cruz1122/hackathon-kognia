from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from threading import Lock

import numpy as np


_pocket_model = None
_pocket_voice_state = None
_pocket_lock = Lock()


def _get_pocket_tts():
    global _pocket_model, _pocket_voice_state
    if _pocket_model is not None:
        return _pocket_model, _pocket_voice_state
    with _pocket_lock:
        if _pocket_model is None:
            from pocket_tts import TTSModel

            model = TTSModel.load_model(
                language=os.getenv("POCKET_TTS_LANGUAGE", "spanish_24l"),
                quantize=True,
            )
            _pocket_model = model
            _pocket_voice_state = model.get_state_for_audio_prompt(
                os.getenv("POCKET_TTS_VOICE", "alba")
            )
    return _pocket_model, _pocket_voice_state


def preload_pocket_tts() -> None:
    """Load Pocket TTS once; the lock guarantees a single active worker."""
    _get_pocket_tts()


def pocket_sample_rate() -> int:
    model, _ = _get_pocket_tts()
    return int(model.sample_rate)


def _pcm16(chunk) -> bytes:
    audio = chunk.detach().cpu().numpy() if hasattr(chunk, "detach") else np.asarray(chunk)
    audio = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (audio * 32767).astype("<i2").tobytes()


def stream_pocket_audio(text: str) -> Iterator[bytes]:
    """Yield signed little-endian PCM while one shared model generates speech."""
    model, voice_state = _get_pocket_tts()
    with _pocket_lock:
        for chunk in model.generate_audio_stream(voice_state, text):
            yield _pcm16(chunk)


def synthesize_text(text: str) -> bytes:
    """Generate WAV audio with the local espeak-ng executable."""
    executable = os.getenv("TTS_EXECUTABLE", "espeak-ng")
    if shutil.which(executable) is None:
        raise FileNotFoundError(executable)
    result = subprocess.run(
        [
            executable,
            "--stdout",
            "-v",
            os.getenv("TTS_VOICE", "es-419"),
            "-s",
            os.getenv("TTS_SPEED", "145"),
            "-p",
            os.getenv("TTS_PITCH", "55"),
        ],
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    if result.returncode != 0 or not result.stdout.startswith(b"RIFF"):
        raise RuntimeError("Local TTS failed")
    return result.stdout
