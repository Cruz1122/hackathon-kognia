from __future__ import annotations

import array
import io
import wave

CANONICAL_RATE = 16000


def timeline_ms(origin_ms: int, samples: int, sample_rate: int = CANONICAL_RATE) -> int:
    """Map a PCM sample index onto the call clock that starts with the recording."""
    return max(0, origin_ms) + (max(0, samples) * 1000) // sample_rate


def resolve_byte_order(media_format: dict) -> str:
    """Use an explicit endian field when Telnyx sends one.

    The media WebSocket L16 payload from the real call is little-endian.
    Only swap when the start event says the wire order is big-endian.
    """
    for key in ("byte_order", "endian", "endianness"):
        raw = str(media_format.get(key) or "").strip().lower()
        if "big" in raw or raw == "be":
            return "big"
        if "little" in raw or raw in {"le", "lsb"}:
            return "little"
    encoding = str(media_format.get("encoding") or "").upper()
    if encoding.endswith("BE"):
        return "big"
    return "little"


def swap16(pcm: bytes) -> bytes:
    size = len(pcm) - (len(pcm) % 2)
    data = bytearray(pcm[:size])
    data[0::2], data[1::2] = data[1::2], data[0::2]
    return bytes(data)


def _mulaw_decode(mulaw: bytes) -> bytes:
    samples = array.array("h")
    for raw in mulaw:
        value = (~raw) & 0xFF
        sign = value & 0x80
        exponent = (value >> 4) & 0x07
        mantissa = value & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        samples.append(-sample if sign else sample)
    return samples.tobytes()


def _mulaw_encode(pcm16le: bytes) -> bytes:
    samples = array.array("h")
    samples.frombytes(pcm16le[: len(pcm16le) - (len(pcm16le) % 2)])
    out = bytearray()
    for sample in samples:
        sign = 0x80 if sample < 0 else 0
        if sample < 0:
            sample = -sample
        sample = min(sample, 32635) + 0x84
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        out.append((~(sign | (exponent << 4) | mantissa)) & 0xFF)
    return bytes(out)


def resample_pcm16le(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return pcm[: len(pcm) - (len(pcm) % 2)]
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return b""
    out_len = max(1, int(round(len(samples) * target_rate / source_rate)))
    out = array.array("h")
    last = len(samples) - 1
    for index in range(out_len):
        position = index * source_rate / target_rate
        left = int(position)
        if left >= last:
            out.append(samples[last])
            continue
        fraction = position - left
        mixed = samples[left] * (1 - fraction) + samples[left + 1] * fraction
        out.append(int(max(-32768, min(32767, round(mixed)))))
    return out.tobytes()


def _to_mono(pcm: bytes, channels: int) -> bytes:
    if channels <= 1:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    mono = array.array("h")
    for index in range(0, len(samples) - channels + 1, channels):
        frame = samples[index : index + channels]
        mono.append(int(sum(frame) / len(frame)))
    return mono.tobytes()


def wire_to_pcm16le(payload: bytes, media_format: dict) -> bytes:
    encoding = str(media_format.get("encoding") or "L16").upper()
    sample_rate = int(media_format.get("sample_rate") or CANONICAL_RATE)
    channels = int(media_format.get("channels") or 1)
    if encoding in {"PCMU", "G711U", "G711_ULAW", "ULAW"}:
        pcm = _mulaw_decode(payload)
        sample_rate = sample_rate or 8000
    else:
        pcm = payload
        if resolve_byte_order(media_format) == "big":
            pcm = swap16(pcm)
    pcm = _to_mono(pcm, channels)
    return resample_pcm16le(pcm, sample_rate or CANONICAL_RATE, CANONICAL_RATE)


def pcm16le_to_wire(pcm: bytes, media_format: dict) -> bytes:
    encoding = str(media_format.get("encoding") or "L16").upper()
    sample_rate = int(media_format.get("sample_rate") or CANONICAL_RATE)
    if encoding in {"PCMU", "G711U", "G711_ULAW", "ULAW"}:
        return _mulaw_encode(resample_pcm16le(pcm, CANONICAL_RATE, 8000))
    audio = resample_pcm16le(pcm, CANONICAL_RATE, sample_rate or CANONICAL_RATE)
    if resolve_byte_order(media_format) == "big":
        audio = swap16(audio)
    return audio


def pcm16le_rms(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    energy = sum(sample * sample for sample in samples) / len(samples)
    return (energy ** 0.5) / 32768.0


def wav_bytes(pcm: bytes, sample_rate: int = CANONICAL_RATE, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _pcm_samples(pcm: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return samples


def mixed_call_wav(customer: bytes, agent: bytes, sample_rate: int = CANONICAL_RATE) -> bytes:
    """Stereo call: channel 0 is the caller, channel 1 is the assistant."""
    caller = _pcm_samples(customer)
    played = _pcm_samples(agent)
    length = max(len(caller), len(played))
    mixed = array.array("h")
    for index in range(length):
        mixed.append(caller[index] if index < len(caller) else 0)
        mixed.append(played[index] if index < len(played) else 0)
    return wav_bytes(mixed.tobytes(), sample_rate, channels=2)


def waveform_levels(pcm: bytes, sample_rate: int = CANONICAL_RATE, channels: int = 1) -> dict[str, list[float]]:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    window = max(1, int(sample_rate * channels * 0.2))
    peaks: list[float] = []
    rms: list[float] = []
    for index in range(0, len(samples), window):
        frame = samples[index : index + window]
        if not frame:
            continue
        peaks.append(round(max(abs(sample) for sample in frame) / 32768.0, 4))
        rms.append(round(((sum(sample * sample for sample in frame) / len(frame)) ** 0.5) / 32768.0, 4))
    return {"peaks": peaks, "rms": rms}
