import math
import struct

from app.features.transcription.service import pcm_wave_level, polish_spanish_punctuation


def _tone(hz: float, samples: int = 1600, rate: int = 16000) -> bytes:
    values = [int(20000 * math.sin(2 * math.pi * hz * index / rate)) for index in range(samples)]
    return struct.pack(f"<{len(values)}h", *values)


def test_pcm_wave_level_is_quiet_for_silence() -> None:
    assert pcm_wave_level(b"\x00\x00" * 1600) <= 0.05


def test_pcm_wave_level_detects_voice_band_tone() -> None:
    assert pcm_wave_level(_tone(220)) > 0.2


def test_pcm_speech_features_marks_voice_tone_as_voiced() -> None:
    from app.features.transcription.service import pcm_speech_features

    _level, voiced, rms = pcm_speech_features(_tone(220))
    assert voiced is True
    assert rms > 0.01


def test_pcm_speech_features_rejects_silence() -> None:
    from app.features.transcription.service import pcm_speech_features

    _level, voiced, rms = pcm_speech_features(b"\x00\x00" * 1600)
    assert voiced is False
    assert rms == 0.0


def test_polish_spanish_punctuation_closes_open_questions() -> None:
    assert polish_spanish_punctuation("Hola, ¿qué puedes hacer") == "Hola, ¿qué puedes hacer?"
    assert polish_spanish_punctuation("¿qué puedes hacer.") == "¿qué puedes hacer?"
    assert polish_spanish_punctuation("¿qué puedes hacer?") == "¿qué puedes hacer?"
    assert polish_spanish_punctuation("Hola, bien") == "Hola, bien"
    assert polish_spanish_punctuation("¡qué bien") == "¡qué bien!"
