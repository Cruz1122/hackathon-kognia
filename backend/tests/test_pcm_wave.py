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


def test_strip_recovery_trailer_keeps_the_short_reply() -> None:
    from app.features.transcription.service import strip_recovery_trailer

    assert strip_recovery_trailer("Si, entiendo") == "Si"
    assert strip_recovery_trailer("No, entiendo.") == "No"
    assert strip_recovery_trailer("Entiendo") == ""
    assert strip_recovery_trailer("quiero una mesa") == "quiero una mesa"


def test_recent_speech_tail_keeps_only_a_fresh_overlap() -> None:
    from app.features.transcription.service import recent_speech_tail

    speech = b"\x01\x00" * 100
    assert recent_speech_tail(speech, last_speech_at=10.0, now=10.4, sample_rate=16000) == speech
    assert recent_speech_tail(speech, last_speech_at=10.0, now=10.6, sample_rate=16000) == b""
    assert recent_speech_tail(speech, last_speech_at=0.0, now=10.0, sample_rate=16000) == b""


def test_pcm_speech_features_rejects_silence() -> None:
    from app.features.transcription.service import pcm_speech_features

    _level, voiced, rms = pcm_speech_features(b"\x00\x00" * 1600)
    assert voiced is False
    assert rms == 0.0


def _quiet_noise(samples: int = 1600) -> bytes:
    values = [40 if index % 2 == 0 else -40 for index in range(samples)]
    return struct.pack(f"<{samples}h", *values)


def test_speech_sanitizer_keeps_a_voiced_tone() -> None:
    from app.features.transcription.service import SpeechSanitizer

    tone = _tone(220)
    cleaned = SpeechSanitizer(0.01).sanitize(tone, 16000)
    assert len(cleaned) == len(tone)
    assert any(cleaned)


def test_speech_sanitizer_zeroes_quiet_noise() -> None:
    from app.features.transcription.service import SpeechSanitizer

    noise = _quiet_noise()
    cleaned = SpeechSanitizer(0.01).sanitize(noise, 16000)
    assert cleaned == b"\x00\x00" * (len(noise) // 2)


def test_speech_sanitizer_holds_a_frame_after_speech() -> None:
    from app.features.transcription.service import SpeechSanitizer

    gate = SpeechSanitizer(0.01)
    noise = _quiet_noise()
    assert gate.sanitize(_tone(220), 16000)
    held = gate.sanitize(noise, 16000)
    assert held != b"\x00\x00" * (len(noise) // 2)
    assert SpeechSanitizer(0.01).sanitize(noise, 16000) == b"\x00\x00" * (len(noise) // 2)


def test_polish_spanish_punctuation_closes_open_questions() -> None:
    assert polish_spanish_punctuation("Hola, ¿qué puedes hacer") == "Hola, ¿qué puedes hacer?"
    assert polish_spanish_punctuation("¿qué puedes hacer.") == "¿qué puedes hacer?"
    assert polish_spanish_punctuation("¿qué puedes hacer?") == "¿qué puedes hacer?"
    assert polish_spanish_punctuation("Hola, bien") == "Hola, bien"
    assert polish_spanish_punctuation("¡qué bien") == "¡qué bien!"
