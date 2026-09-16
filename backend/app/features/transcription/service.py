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

SHERPA_CONFIG: dict[str, Any] = {
    "sample_rate": 16000,
    "feature_dim": 80,
    "low_freq": 80.0,
    "high_freq": -400.0,
    "dither": 0.0,
    "decoding_method": "greedy_search",
    "max_active_paths": 4,
    "blank_penalty": 0.4,
    "temperature_scale": 1.2,
    "provider": "cpu",
    "enable_endpoint_detection": True,
    "rule1_min_trailing_silence": 100.0,
    "rule2_min_trailing_silence": 1.0,
    "rule3_min_utterance_length": 20,
    "finish_padding_seconds": 0.5,
    "hot_frame_peak_gate": 0.0,
    "hot_frame_target_rms": 0.10,
    "backend": "online_transducer",
    "offline_partial_seconds": 0.4,
    "canary_src_lang": "es",
    "canary_tgt_lang": "es",
    "language": "es",
    "speculative_flush_seconds": 0.0,
}


def stt_label() -> str:
    directory = _model_dir()
    name = directory.name
    readme = ""
    path = directory / "README.md"
    if path.is_file():
        readme = path.read_text(encoding="utf-8", errors="replace")[:800]
    blob = f"{name}\n{readme}".lower()
    if "nemotron" in blob:
        return "Nemotron 3.5 streaming 560 ms"
    if "kroko" in blob or "zipformer-es" in blob:
        return "Kroko Zipformer ES"
    return name


def _model_dir() -> Path:
    configured = os.getenv("SHERPA_MODEL_DIR", "backend/models/sherpa-nemotron-35-560")
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


class _OfflinePcmBuffer:
    """Accumulates float32 PCM for local offline recognizers (NeMo CTC, etc.)."""

    def __init__(self) -> None:
        self.samples = np.zeros(0, dtype=np.float32)
        self.last_text = ""
        self.last_decode_n = 0


class _FlushOnlineStream:
    """Live Sherpa stream plus a PCM buffer for speculative silence flush."""

    def __init__(self, live: Any) -> None:
        self.live = live
        self.samples = np.zeros(0, dtype=np.float32)
        self.last_text = ""
        self.last_decode_n = 0


def _backend(config: dict[str, Any] | None = None) -> str:
    cfg = config or SHERPA_CONFIG
    backend = str(cfg.get("backend") or os.getenv("SHERPA_BACKEND", "online_transducer"))
    if backend != "auto":
        return backend
    directory = _model_dir()
    if list(directory.glob("encoder*.onnx")):
        return "online_transducer"
    if (directory / "model.int8.onnx").is_file() or (directory / "model.onnx").is_file():
        return "offline_nemo_ctc"
    if (directory / "encoder_model.ort").is_file() and (directory / "decoder_model_merged.ort").is_file():
        return "offline_moonshine"
    return "online_transducer"


def build_recognizer(*, overrides: dict[str, Any] | None = None) -> Any:
    import sherpa_onnx

    config = {**SHERPA_CONFIG, **(overrides or {})}
    directory = _model_dir()
    tokens = directory / "tokens.txt"
    if not tokens.is_file():
        raise FileNotFoundError(f"Falta el modelo Sherpa en {directory}. Ejecuta scripts/download-sherpa-model.sh")
    threads = int(os.getenv("SHERPA_THREADS", "2"))
    backend = _backend(config)
    if backend == "offline_nemo_ctc":
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(_first_file(directory, "model.int8.onnx", "model.onnx")),
            tokens=str(tokens),
            num_threads=threads,
            sample_rate=int(config["sample_rate"]),
            feature_dim=int(config["feature_dim"]),
            decoding_method="greedy_search",
            provider=str(config["provider"]),
        )
    if backend == "offline_nemo_transducer":
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(_first_file(directory, "encoder*.int8.onnx", "encoder*.onnx")),
            decoder=str(_first_file(directory, "decoder*.int8.onnx", "decoder*.onnx")),
            joiner=str(_first_file(directory, "joiner*.int8.onnx", "joiner*.onnx")),
            tokens=str(tokens),
            num_threads=threads,
            sample_rate=int(config["sample_rate"]),
            feature_dim=int(config["feature_dim"]),
            decoding_method="greedy_search",
            provider=str(config["provider"]),
            model_type="nemo_transducer",
        )
    if backend == "offline_moonshine":
        return sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=str(_first_file(directory, "encoder_model.ort", "encoder*.ort")),
            decoder=str(_first_file(directory, "decoder_model_merged.ort", "decoder*.ort")),
            tokens=str(tokens),
            num_threads=threads,
            decoding_method="greedy_search",
            provider=str(config["provider"]),
        )
    if backend == "offline_nemo_canary":
        return sherpa_onnx.OfflineRecognizer.from_nemo_canary(
            encoder=str(_first_file(directory, "encoder*.int8.onnx", "encoder*.onnx")),
            decoder=str(_first_file(directory, "decoder*.int8.onnx", "decoder*.onnx")),
            tokens=str(tokens),
            src_lang=str(config.get("canary_src_lang", "es")),
            tgt_lang=str(config.get("canary_tgt_lang", "es")),
            num_threads=threads,
            sample_rate=int(config["sample_rate"]),
            decoding_method="greedy_search",
            provider=str(config["provider"]),
        )
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(tokens),
        encoder=str(_first_file(directory, "encoder*.int8.onnx", "encoder*.onnx")),
        decoder=str(_first_file(directory, "decoder*.int8.onnx", "decoder*.onnx")),
        joiner=str(_first_file(directory, "joiner*.int8.onnx", "joiner*.onnx")),
        num_threads=threads,
        sample_rate=int(config["sample_rate"]),
        feature_dim=int(config["feature_dim"]),
        low_freq=float(config["low_freq"]),
        high_freq=float(config["high_freq"]),
        dither=float(config["dither"]),
        decoding_method=str(config["decoding_method"]),
        max_active_paths=int(config["max_active_paths"]),
        blank_penalty=float(config["blank_penalty"]),
        temperature_scale=float(config["temperature_scale"]),
        provider=str(config["provider"]),
        enable_endpoint_detection=bool(config["enable_endpoint_detection"]),
        rule1_min_trailing_silence=float(config["rule1_min_trailing_silence"]),
        rule2_min_trailing_silence=float(config["rule2_min_trailing_silence"]),
        rule3_min_utterance_length=int(config["rule3_min_utterance_length"]),
    )


def apply_config(overrides: dict[str, Any]) -> None:
    """Update Sherpa knobs and drop the cached recognizer (eval experiments)."""
    global _recognizer
    SHERPA_CONFIG.update(overrides)
    with _recognizer_lock:
        _recognizer = None


def _get_recognizer() -> Any:
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    with _recognizer_lock:
        if _recognizer is None:
            _recognizer = build_recognizer()
    return _recognizer


def preload_model() -> None:
    """Load Sherpa-ONNX before the API starts accepting traffic."""
    _get_recognizer()


def _attach_language(stream: Any) -> None:
    language = str(SHERPA_CONFIG.get("language") or "").strip()
    if language:
        stream.set_option("language", language)


def create_stream() -> Any:
    if _backend().startswith("offline_"):
        _get_recognizer()
        return _OfflinePcmBuffer()
    live = _get_recognizer().create_stream()
    _attach_language(live)
    if float(SHERPA_CONFIG.get("speculative_flush_seconds") or 0) > 0:
        return _FlushOnlineStream(live)
    return live


def reset_stream(stream: Any) -> None:
    if isinstance(stream, (_OfflinePcmBuffer, _FlushOnlineStream)):
        stream.samples = np.zeros(0, dtype=np.float32)
        stream.last_text = ""
        stream.last_decode_n = 0
        if isinstance(stream, _FlushOnlineStream):
            _get_recognizer().reset(stream.live)
            _attach_language(stream.live)
        return
    _get_recognizer().reset(stream)


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


def _compress_hot_frame(samples: np.ndarray) -> np.ndarray:
    """Scale a chunk toward target RMS only when it is near clipping (exp-014 KEEP)."""
    gate = float(SHERPA_CONFIG.get("hot_frame_peak_gate", 0.0))
    if samples.size == 0 or gate <= 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak <= gate:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1e-5:
        return samples
    target = float(SHERPA_CONFIG.get("hot_frame_target_rms", 0.10))
    scaled = np.clip(samples * min(target / rms, 8.0), -1.0, 1.0)
    quantized = np.clip(np.round(scaled * 32768.0), -32768, 32767).astype(np.int16)
    return quantized.astype(np.float32) / 32768.0


def _decode_offline_samples(samples: np.ndarray, sample_rate: int) -> str:
    if samples.size == 0:
        return ""
    recognizer = _get_recognizer()
    offline = recognizer.create_stream()
    offline.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(offline)
    return _result_text(getattr(offline, "result", None) or recognizer.get_result(offline))


def _decode_online_with_flush(samples: np.ndarray, sample_rate: int) -> str:
    """Throwaway stream: real PCM + instant zero-pad, then decode (does not touch the live stream)."""
    if samples.size == 0:
        return ""
    recognizer = _get_recognizer()
    speculative = recognizer.create_stream()
    _attach_language(speculative)
    speculative.accept_waveform(sample_rate, samples)
    flush = float(SHERPA_CONFIG.get("speculative_flush_seconds") or 0)
    if flush > 0:
        speculative.accept_waveform(sample_rate, np.zeros(int(sample_rate * flush), dtype=np.float32))
    speculative.input_finished()
    while recognizer.is_ready(speculative):
        recognizer.decode_stream(speculative)
    return _result_text(recognizer.get_result(speculative))


def feed_pcm(stream: Any, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
    """Push one PCM int16 chunk and return (partial_text, is_endpoint)."""
    samples = _compress_hot_frame(_pcm_to_float(pcm))
    if samples.size == 0:
        return "", False
    if isinstance(stream, _OfflinePcmBuffer):
        stream.samples = np.concatenate([stream.samples, samples])
        stride = int(sample_rate * float(SHERPA_CONFIG.get("offline_partial_seconds", 0.4)))
        if stream.samples.size >= stride and stream.samples.size - stream.last_decode_n >= stride:
            stream.last_text = _decode_offline_samples(stream.samples, sample_rate)
            stream.last_decode_n = stream.samples.size
        return stream.last_text, False
    recognizer = _get_recognizer()
    live = stream.live if isinstance(stream, _FlushOnlineStream) else stream
    live.accept_waveform(sample_rate, samples)
    while recognizer.is_ready(live):
        recognizer.decode_stream(live)
    live_text = _result_text(recognizer.get_result(live))
    ended = bool(recognizer.is_endpoint(live))
    if isinstance(stream, _FlushOnlineStream):
        stream.samples = np.concatenate([stream.samples, samples])
        stride = int(sample_rate * float(SHERPA_CONFIG.get("offline_partial_seconds", 0.4)))
        if stream.samples.size >= stride and stream.samples.size - stream.last_decode_n >= stride:
            stream.last_text = _decode_online_with_flush(stream.samples, sample_rate)
            stream.last_decode_n = stream.samples.size
        return stream.last_text or live_text, ended
    return live_text, ended


def finish_stream(stream: Any, sample_rate: int = 16000) -> str:
    if isinstance(stream, _OfflinePcmBuffer):
        text = _decode_offline_samples(stream.samples, sample_rate)
        stream.last_text = text
        return text
    recognizer = _get_recognizer()
    live = stream.live if isinstance(stream, _FlushOnlineStream) else stream
    padding = np.zeros(int(sample_rate * float(SHERPA_CONFIG["finish_padding_seconds"])), dtype=np.float32)
    live.accept_waveform(sample_rate, padding)
    live.input_finished()
    while recognizer.is_ready(live):
        recognizer.decode_stream(live)
    return _result_text(recognizer.get_result(live))


def pcm_speech_features(pcm: bytes, sample_rate: int = 16000) -> tuple[float, bool, float]:
    """Return (visual_level, voiced, rms) for barge-in and STT gating."""
    count = len(pcm) // 2
    if count < 8:
        return 0.04, False, 0.0
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
        return max(0.0, min(1.0, rms * 14)), False, rms
    pitch_hz = (crossings * sample_rate) / (2 * count)
    pitch = min(1.0, max(0.0, (min(500.0, max(70.0, pitch_hz)) - 70.0) / 430.0))
    level = max(0.0, min(1.0, rms * 5 * (0.45 + 0.55 * pitch)))
    voiced = rms >= 0.016 and 85.0 <= pitch_hz <= 340.0
    return level, voiced, rms


def pcm_wave_level(pcm: bytes, sample_rate: int = 16000) -> float:
    """Map signed-int16 PCM to a 0-1 speech envelope for the live spectrogram."""
    level, _voiced, _rms = pcm_speech_features(pcm, sample_rate)
    return level


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


def decode_audio_file(path: Path) -> bytes:
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
        return transcribe_pcm(decode_audio_file(path), 16000)
    finally:
        path.unlink(missing_ok=True)
