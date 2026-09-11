import math
import struct

from app.features.transcription.service import pcm_wave_level


def _tone(hz: float, samples: int = 1600, rate: int = 16000) -> bytes:
    values = [int(20000 * math.sin(2 * math.pi * hz * index / rate)) for index in range(samples)]
    return struct.pack(f"<{len(values)}h", *values)


def test_pcm_wave_level_is_quiet_for_silence() -> None:
    assert pcm_wave_level(b"\x00\x00" * 1600) <= 0.05


def test_pcm_wave_level_detects_voice_band_tone() -> None:
    assert pcm_wave_level(_tone(220)) > 0.2
