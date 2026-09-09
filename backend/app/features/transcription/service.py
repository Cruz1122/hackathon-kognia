from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

_model: Any | None = None
_model_lock = Lock()


def _get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                os.getenv("WHISPER_MODEL", "tiny"),
                device=os.getenv("WHISPER_DEVICE", "cpu"),
                compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            )
    return _model


def preload_model() -> None:
    """Load the configured model before the API starts accepting traffic."""
    _get_model()


def transcribe_audio(audio: bytes, content_type: str) -> str:
    """Transcribe one short browser recording with a local low-resource model."""
    suffix = ".ogg" if "ogg" in content_type else ".wav" if "wav" in content_type else ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(audio)
        path = Path(temporary.name)
    try:
        segments, _ = _get_model().transcribe(
            str(path),
            language=os.getenv("WHISPER_LANGUAGE", "es"),
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    finally:
        path.unlink(missing_ok=True)
