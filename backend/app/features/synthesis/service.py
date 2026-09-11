from __future__ import annotations

import io
import logging
import os
import tempfile
import wave
from collections.abc import Iterator
from threading import Lock

import numpy as np


logger = logging.getLogger("hackathon.voice")

_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_DEFAULT_REF_AUDIO = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-TTS-Repo/clone.wav"
_DEFAULT_REF_TEXT = (
    "Okay. Yeah. I resent you. I love you. I respect you. "
    "But you know what? You blew it! And thanks to you."
)
_GEN_KWARGS = {
    "non_streaming_mode": False,
    "do_sample": True,
    "temperature": 0.7,
    "top_k": 20,
    "max_new_tokens": 512,
}

_lock = Lock()
_model = None
_voice_clone_prompt = None
_sample_rate = 24_000


def _language() -> str:
    return os.getenv("QWEN_TTS_LANGUAGE", "Spanish")


def _speaker() -> str:
    return os.getenv("QWEN_TTS_SPEAKER", "Serena")


def _can_use_bfloat16(torch) -> bool:
    return bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())


def _can_use_flash_attention(torch) -> bool:
    if not torch.cuda.is_available():
        return False
    try:
        import flash_attn  # noqa: F401
    except ImportError:
        return False
    major, _minor = torch.cuda.get_device_capability()
    return major >= 8


def _from_pretrained_kwargs(torch) -> dict:
    if not torch.cuda.is_available():
        logger.info("CUDA not detected; loading Qwen3-TTS on CPU")
        return {"device_map": "cpu"}

    kwargs: dict = {"device_map": "cuda:0"}
    use_bf16 = _can_use_bfloat16(torch)
    if use_bf16:
        kwargs["dtype"] = torch.bfloat16
        logger.info("Loading Qwen3-TTS with bfloat16")
    if use_bf16 and _can_use_flash_attention(torch):
        kwargs["attn_implementation"] = "flash_attention_2"
        logger.info("Loading Qwen3-TTS with FlashAttention 2")
    return kwargs


def _load_model(torch, Qwen3TTSModel):
    model_id = os.getenv("QWEN_TTS_MODEL", _MODEL_ID)
    kwargs = _from_pretrained_kwargs(torch)
    try:
        return Qwen3TTSModel.from_pretrained(model_id, **kwargs)
    except Exception:
        if "attn_implementation" not in kwargs:
            raise
        logger.warning("FlashAttention 2 failed during load; retrying without it")
        kwargs.pop("attn_implementation")
        return Qwen3TTSModel.from_pretrained(model_id, **kwargs)


def _is_base_model(model) -> bool:
    return str(getattr(model.model, "tts_model_type", "")).lower() == "base"


def _pcm16(chunk) -> bytes:
    audio = chunk.detach().cpu().numpy() if hasattr(chunk, "detach") else np.asarray(chunk)
    audio = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (audio * 32767).astype("<i2").tobytes()


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _generate_wavs(model, text: str):
    if _is_base_model(model):
        return model.generate_voice_clone(
            text=text,
            language=_language(),
            voice_clone_prompt=_voice_clone_prompt,
            **_GEN_KWARGS,
        )
    return model.generate_custom_voice(
        text=text,
        language=_language(),
        speaker=_speaker(),
        **_GEN_KWARGS,
    )


def _generate_pcm(text: str) -> tuple[bytes, int]:
    model, _prompt = _get_qwen_tts()
    with _lock:
        wavs, sample_rate = _generate_wavs(model, text)
        global _sample_rate
        _sample_rate = int(sample_rate)
    return _pcm16(wavs[0]), _sample_rate


def _get_qwen_tts():
    global _model, _voice_clone_prompt, _sample_rate
    if _model is not None:
        return _model, _voice_clone_prompt
    with _lock:
        if _model is None:
            import torch

            cache_dir = os.path.join(tempfile.gettempdir(), "qwen-tts-numba")
            os.makedirs(cache_dir, exist_ok=True)
            os.environ.setdefault("NUMBA_CACHE_DIR", cache_dir)
            from qwen_tts import Qwen3TTSModel

            model = _load_model(torch, Qwen3TTSModel)
            prompt = None
            if _is_base_model(model):
                prompt = model.create_voice_clone_prompt(
                    ref_audio=os.getenv("QWEN_TTS_REF_AUDIO", _DEFAULT_REF_AUDIO),
                    ref_text=os.getenv("QWEN_TTS_REF_TEXT", _DEFAULT_REF_TEXT),
                    x_vector_only_mode=True,
                )
            tokenizer = getattr(model.model, "speech_tokenizer", None)
            sample_rate = getattr(tokenizer, "sample_rate", None) or getattr(
                tokenizer, "sampling_rate", _sample_rate
            )
            _voice_clone_prompt = prompt
            wavs, generated_rate = _generate_wavs(model, "Listo.")
            del wavs
            _model = model
            _sample_rate = int(generated_rate or sample_rate)
            device = getattr(model, "device", "unknown")
            logger.info(
                "Qwen3-TTS resident on %s at %s Hz (%s)",
                device,
                _sample_rate,
                getattr(model.model, "tts_model_type", "unknown"),
            )
    return _model, _voice_clone_prompt


def preload_tts() -> None:
    """Load Qwen3-TTS once and keep it resident for later phone-call turns."""
    _get_qwen_tts()


def tts_sample_rate() -> int:
    _get_qwen_tts()
    return int(_sample_rate)


def stream_tts_audio(text: str) -> Iterator[bytes]:
    """Yield signed little-endian PCM in 20 ms frames from one shared GPU/CPU worker."""
    pcm, sample_rate = _generate_pcm(text)
    frame = max(int(sample_rate * 0.02), 1) * 2
    for index in range(0, len(pcm), frame):
        yield pcm[index : index + frame]


def synthesize_text(text: str) -> bytes:
    """Generate a mono WAV from the resident Qwen3-TTS model."""
    pcm, sample_rate = _generate_pcm(text)
    return _wav_bytes(pcm, sample_rate)
