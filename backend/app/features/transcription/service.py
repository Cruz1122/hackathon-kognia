from __future__ import annotations

import array
import os
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

_recognizer: Any | None = None
_recognizer_lock = Lock()

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _model_dir() -> Path:
    configured = os.getenv("SHERPA_MODEL_DIR", "backend/models/sherpa-es")
    path = Path(configured)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path


def _first_file(directory: Path, *patterns: str) -> Path:
    for pattern in patterns:
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No se encontró {patterns} en {directory}")


def _get_recognizer() -> Any:
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    with _recognizer_lock:
        if _recognizer is None:
            import sherpa_onnx

            directory = _model_dir()
            tokens = directory / "tokens.txt"
            if not tokens.is_file():
                raise FileNotFoundError(
                    f"Falta el modelo Sherpa en {directory}. Ejecuta scripts/download-sherpa-model.sh"
                )
            _recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(tokens),
                encoder=str(_first_file(directory, "encoder*.int8.onnx", "encoder*.onnx")),
                decoder=str(_first_file(directory, "decoder*.onnx")),
                joiner=str(_first_file(directory, "joiner*.int8.onnx", "joiner*.onnx")),
                num_threads=int(os.getenv("SHERPA_THREADS", "2")),
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=100.0,
                rule2_min_trailing_silence=1.0,
                rule3_min_utterance_length=20,
            )
    return _recognizer


def preload_model() -> None:
    """Load Sherpa-ONNX before the API starts accepting traffic."""
    _get_recognizer()


def create_stream() -> Any:
    return _get_recognizer().create_stream()


def reset_stream(stream: Any) -> None:
    recognizer = _get_recognizer()
    recognizer.reset(stream)


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    text = getattr(result, "text", None)
    return str(text).strip() if text else ""


def _pcm_to_float(pcm: bytes) -> np.ndarray:
    usable = pcm[: len(pcm) - len(pcm) % 2]
    if not usable:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(usable, dtype=np.int16).astype(np.float32) / 32768.0


def feed_pcm(stream: Any, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
    """Push one PCM int16 chunk and return (partial_text, is_endpoint)."""
    samples = _pcm_to_float(pcm)
    if samples.size == 0:
        return "", False
    recognizer = _get_recognizer()
    stream.accept_waveform(sample_rate, samples)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    text = _result_text(recognizer.get_result(stream))
    ended = bool(recognizer.is_endpoint(stream))
    return text, ended


def finish_stream(stream: Any, sample_rate: int = 16000) -> str:
    recognizer = _get_recognizer()
    padding = np.zeros(int(sample_rate * 0.5), dtype=np.float32)
    stream.accept_waveform(sample_rate, padding)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return _result_text(recognizer.get_result(stream))


def pcm_wave_level(pcm: bytes, sample_rate: int = 16000) -> float:
    """Map signed-int16 PCM to a 0-1 speech envelope for the live spectrogram."""
    count = len(pcm) // 2
    if count < 8:
        return 0.04
    samples = array.array("h")
    samples.frombytes(pcm[: count * 2])
    energy = 0
    crossings = 0
    previous = 0
    for sample in samples:
        energy += sample * sample
        if (previous >= 0) != (sample >= 0):
            crossings += 1
        previous = sample
    rms = (energy / count) ** 0.5 / 32768.0
    if rms < 0.003:
        return max(0.0, min(1.0, rms * 14))
    pitch_hz = (crossings * sample_rate) / (2 * count)
    pitch = min(1.0, max(0.0, (min(500.0, max(70.0, pitch_hz)) - 70.0) / 430.0))
    return max(0.0, min(1.0, rms * 10 * (0.45 + 0.55 * pitch)))


def transcribe_pcm(pcm: bytes, sample_rate: int = 16000, *, vad: bool = True) -> str:
    """Transcribe raw mono signed-int16 PCM with a one-shot Sherpa stream."""
    del vad
    if len(pcm) < sample_rate // 4:
        return ""
    stream = create_stream()
    feed_pcm(stream, pcm, sample_rate)
    return finish_stream(stream, sample_rate)


def _audio_suffix(content_type: str) -> str:
    ctype = content_type.lower()
    if "webm" in ctype:
        return ".webm"
    if "mpeg" in ctype or "mp3" in ctype:
        return ".mp3"
    if "wav" in ctype:
        return ".wav"
    if "ogg" in ctype or "opus" in ctype:
        return ".ogg"
    if "mp4" in ctype or "m4a" in ctype or "aac" in ctype:
        return ".m4a"
    return ".webm"


def _decode_to_pcm(path: Path) -> bytes:
    completed = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace") or "ffmpeg no pudo decodificar el audio.")
    return completed.stdout


def transcribe_audio(audio: bytes, content_type: str) -> str:
    """Transcribe one short browser recording with Sherpa-ONNX."""
    suffix = _audio_suffix(content_type)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(audio)
        path = Path(temporary.name)
    try:
        return transcribe_pcm(_decode_to_pcm(path), 16000)
    finally:
        path.unlink(missing_ok=True)
