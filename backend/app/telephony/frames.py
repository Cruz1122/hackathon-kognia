from __future__ import annotations

import struct

FRAME_VERSION = 1
CHANNEL_CUSTOMER = 1
CHANNEL_AGENT = 2


def encode_audio_frame(*, channel: int, flags: int, seq: int, offset_ms: int, pcm: bytes) -> bytes:
    header = struct.pack("!BBBII", FRAME_VERSION, channel, flags, seq & 0xFFFFFFFF, offset_ms & 0xFFFFFFFF)
    return header + pcm


def decode_audio_frame(frame: bytes) -> dict[str, int | bytes]:
    version, channel, flags, seq, offset_ms = struct.unpack("!BBBII", frame[:11])
    return {
        "version": version,
        "channel": channel,
        "flags": flags,
        "seq": seq,
        "offset_ms": offset_ms,
        "pcm": frame[11:],
    }
