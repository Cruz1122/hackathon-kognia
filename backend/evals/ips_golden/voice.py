"""Optional voice timing contract for Golden runs.

It is intentionally transport-free: callers can pass the real STT/TTS
providers in live mode or the existing fake providers offline.  Durations are
recorded by ``TraceRecorder`` (``perf_counter`` internally), with STT and
first-audio-to-TTS as separate signals.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.platform.tracing import TraceRecorder


async def measure_voice_turn(
    audio: bytes,
    *,
    content_type: str = "audio/pcm",
    stt: Any,
    tts: Any,
    text_for_tts: str | None = None,
) -> dict[str, Any]:
    recorder = TraceRecorder()
    recorder.start("voice-benchmark", [], [])
    stt_span = recorder.span("voice.stt", {"content_type": content_type})
    try:
        transcript = await asyncio.to_thread(stt.transcribe_audio, audio, content_type)
        recorder.close_span(stt_span, ok=True, transcript_chars=len(str(transcript)))
    except Exception as exc:
        errors = [f"stt:{type(exc).__name__}"]
        recorder.close_span(stt_span, ok=False, error=type(exc).__name__)
        transcript = ""
    else:
        errors = []
    tts_span = recorder.span("voice.tts", {"text_chars": len(text_for_tts or transcript)})
    first_audio_span = recorder.span("voice.tts.first_audio", {})
    first_audio = False
    audio_chunks = 0
    try:
        for chunk in tts.stream_audio(text_for_tts or transcript):
            audio_chunks += 1
            if not first_audio:
                first_audio = True
                recorder.note_first_token(first_audio_span)
            await asyncio.sleep(0)
        recorder.close_span(first_audio_span, ok=first_audio, chunks=audio_chunks)
        recorder.close_span(tts_span, ok=True, chunks=audio_chunks)
    except Exception as exc:
        errors.append(f"tts:{type(exc).__name__}")
        recorder.close_span(first_audio_span, ok=False, error=type(exc).__name__, chunks=audio_chunks)
        recorder.close_span(tts_span, ok=False, error=type(exc).__name__, chunks=audio_chunks)
    recorder.finish(
        answer=str(transcript),
        provider="voice-contract",
        model="fake-or-injected",
        status="error" if errors else "ok",
    )
    trace = recorder.to_dict()
    stt_value = next((span["duration_ms"] for span in trace["spans"] if span["name"] == "voice.stt"), None)
    first_audio_value = next((span["attributes"].get("first_token_ms") for span in trace["spans"] if span["name"] == "voice.tts.first_audio"), None)
    return {
        "transcript": str(transcript),
        "audio_chunks": audio_chunks,
        "stt_ms": stt_value,
        "tts_first_audio_ms": first_audio_value,
        "errors": errors,
        "trace": trace,
    }


__all__ = ["measure_voice_turn"]
