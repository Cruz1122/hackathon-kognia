from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

from ..agent.tools.contracts import ToolContext
from ..features.agent.service import stream_agent
from .audio import CANONICAL_RATE, pcm16le_to_wire, resample_pcm16le
from .frames import CHANNEL_AGENT, encode_audio_frame
from .live_audio import live_audio_hub
from .marks import MarkTracker
from .sessions import CallSession
from .timeline import timeline
from .tts import PiperTTSProvider

logger = logging.getLogger("hackathon.telnyx.bridge")


async def run_agent_turn(
    session: CallSession,
    transcript: str,
    *,
    agent: Any = stream_agent,
    tts: PiperTTSProvider | None = None,
) -> None:
    voice = tts or PiperTTSProvider()
    if session.marks is None:
        session.marks = MarkTracker()
    timeline.record(session, "agent.state", {"state": "thinking"})
    session.agent_state = "thinking"
    tool_context = ToolContext(
        request_id=f"telnyx-{session.call_id}",
        conversation_id=str(session.conversation_id) if session.conversation_id else None,
        organization_id=str(session.organization_id) if session.organization_id else None,
        user_id=str(session.system_user_id) if session.system_user_id else None,
    )
    answer: list[str] = []
    generator = agent(transcript, messages=list(session.history), tool_context=tool_context)
    try:
        async for kind, payload in generator:
            if session.closed:
                break
            await _publish_agent_event(session, kind, payload, answer)
    except asyncio.CancelledError:
        await _cancel_playback(session)
        raise
    finally:
        await generator.aclose()
    spoken = "".join(answer).strip()
    session.history.append({"role": "user", "content": transcript})
    if spoken:
        session.history.append({"role": "assistant", "content": spoken})
        timeline.record(session, "transcript.final", {"speaker": "agent", "text": spoken})
        session.agent_state = "speaking"
        timeline.record(session, "agent.state", {"state": "speaking"})
        await _speak(session, voice, spoken)
    session.agent_state = "listening"
    timeline.record(session, "agent.state", {"state": "listening"})


async def _publish_agent_event(
    session: CallSession,
    kind: str,
    payload: dict[str, Any],
    answer: list[str],
) -> None:
    if kind == "token":
        answer.append(str(payload.get("text") or ""))
        return
    if kind == "tool.started":
        timeline.record(session, "tool.started", payload)
        return
    if kind == "tool.completed":
        timeline.record(session, "tool.completed", payload)
        return
    if kind in {"rag.started", "rag.completed"}:
        timeline.record(session, kind, payload)
        return
    if kind == "error":
        timeline.record(session, "agent.error", {"message": payload.get("message") or "error"})


async def _speak(session: CallSession, voice: PiperTTSProvider, text: str) -> None:
    source_rate = await asyncio.to_thread(voice.sample_rate)
    loop = asyncio.get_running_loop()
    chunks: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=8)

    def produce() -> None:
        try:
            for chunk in voice.stream_audio(text):
                asyncio.run_coroutine_threadsafe(chunks.put(chunk), loop).result()
        except Exception:
            logger.exception("Piper stream failed")
        finally:
            asyncio.run_coroutine_threadsafe(chunks.put(None), loop).result()

    producer = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            chunk = await chunks.get()
            if chunk is None or session.closed:
                break
            canonical = resample_pcm16le(chunk, source_rate, CANONICAL_RATE)
            await emit_agent_audio(session, canonical)
    except asyncio.CancelledError:
        await _cancel_playback(session)
        raise
    finally:
        session.agent_segment_open = False
        producer.cancel()


async def emit_agent_audio(session: CallSession, pcm: bytes) -> None:
    if not pcm:
        return
    customer_samples = len(session.customer_pcm) // 2
    agent_samples = len(session.agent_pcm) // 2
    if not session.agent_segment_open and customer_samples > agent_samples:
        session.agent_pcm.extend(b"\x00\x00" * (customer_samples - agent_samples))
    session.agent_segment_open = True
    start = len(session.agent_pcm)
    session.agent_pcm.extend(pcm)
    media_format = session.media_format or {
        "encoding": "L16",
        "sample_rate": 16000,
        "channels": 1,
        "byte_order": "little",
    }
    if session.organization_id is not None:
        live_audio_hub.publish(
            session.organization_id,
            session.call_id,
            encode_audio_frame(
                channel=CHANNEL_AGENT,
                flags=0,
                seq=session.sequence,
                offset_ms=session.offset_ms(),
                pcm=pcm,
            ),
        )
    websocket = session.websocket
    if websocket is None:
        return
    wire = pcm16le_to_wire(pcm, media_format)
    await websocket.send_json({"event": "media", "media": {"payload": base64.b64encode(wire).decode("ascii")}})
    if session.marks is None:
        return
    name = session.marks.generated()
    session.playback_spans.append((start, len(session.agent_pcm), name))
    timeline.record(session, "audio.generated", {"name": name})
    await websocket.send_json({"event": "mark", "mark": {"name": name}})
    session.marks.sent(name)
    timeline.record(session, "audio.sent", {"name": name})


async def _cancel_playback(session: CallSession) -> None:
    cancelled = session.marks.clear_unplayed() if session.marks is not None else []
    if cancelled:
        muted = bytearray(session.agent_pcm)
        for start, end, name in session.playback_spans:
            if name in cancelled:
                muted[start:end] = b"\x00" * (end - start)
        session.agent_pcm = muted
    for name in cancelled:
        timeline.record(session, "audio.cancelled", {"name": name})
    websocket = session.websocket
    if websocket is not None:
        try:
            await websocket.send_json({"event": "clear"})
        except Exception:
            logger.info("Could not clear Telnyx playback")


def iter_agent(agent: Any, prompt: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    return agent(prompt)
