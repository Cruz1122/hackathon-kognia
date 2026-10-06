from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..agent.tools.contracts import ToolContext
from ..features.agent.service import stream_agent
from .audio import CANONICAL_RATE, pcm16le_to_wire, resample_pcm16le, timeline_ms
from .frames import CHANNEL_AGENT, encode_audio_frame
from .live_audio import live_audio_hub
from .marks import MarkTracker
from .sessions import CallSession
from .timeline import timeline
from .tts import PiperTTSProvider

logger = logging.getLogger("hackathon.telnyx.bridge")


async def _with_holding(generator, session: CallSession, voice):
    from ..agent.phrases import HOLDING, pick

    held = False
    queue = asyncio.Queue(maxsize=32)
    end = object()

    async def produce():
        try:
            # One task owns the entire generator lifetime, including ContextVar tokens.
            async for event in generator:
                await queue.put(event)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await generator.aclose()
            # A cancelled consumer will never drain a full queue.
            if not asyncio.current_task().cancelling():
                await queue.put(end)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=4)
            except asyncio.TimeoutError:
                if producer.done():
                    producer.result()
                    return
                if not held:
                    held = True
                    await _speak(session, voice, pick(HOLDING))
                continue
            if event is end:
                return
            if isinstance(event, Exception):
                raise event
            yield event
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


async def run_agent_turn(
    session: CallSession,
    transcript: str,
    *,
    agent: Any = stream_agent,
    tts: PiperTTSProvider | None = None,
    system_initiated: bool = False,
) -> None:
    voice = tts or PiperTTSProvider()
    if session.marks is None:
        session.marks = MarkTracker()
    customer_turn_offset_ms = next(
        (
            int(event.get("offset_ms") or 0)
            for event in reversed(session.events)
            if event.get("type") == "transcript.final"
            and (event.get("payload") or {}).get("speaker") == "customer"
        ),
        session.offset_ms(),
    )
    timeline.record(session, "agent.state", {"state": "thinking"})
    session.agent_state = "thinking"
    tool_context = ToolContext(
        request_id=f"telnyx-{session.call_id}-{uuid.uuid4()}",
        conversation_id=str(session.conversation_id) if session.conversation_id else None,
        organization_id=str(session.organization_id) if session.organization_id else None,
        user_id=str(session.system_user_id) if session.system_user_id else None,
        channel='voice',
        system_initiated=system_initiated,
    )
    answer: list[str] = []
    proposal_id = None
    reservation_confirmed = False
    session.awaiting_agent_reply = True
    generator = agent(transcript, messages=list(session.history), tool_context=tool_context)
    waiting = _with_holding(generator, session, voice)
    try:
        async for kind, payload in waiting:
            if session.closed:
                break
            if kind == 'done':
                proposal_id = payload.get('proposal_id')
            if (kind == 'tool.completed' and payload.get('tool') == 'create_booking'
                    and payload.get('ok') is True):
                reservation_confirmed = True
            await _publish_agent_event(
                session,
                kind,
                payload,
                answer,
                customer_turn_offset_ms=customer_turn_offset_ms,
            )
    except asyncio.CancelledError:
        await _cancel_playback(session)
        raise
    finally:
        await waiting.aclose()
        await generator.aclose()
    spoken = "".join(answer).strip()
    session.history.append({"role": "user", "content": transcript})
    if spoken:
        session.history.append({"role": "assistant", "content": spoken})
        if agent is stream_agent and session.conversation_id and session.organization_id:
            from ..db.models import Message, MessageRole
            from ..db.session import get_session_factory
            async with get_session_factory()() as db:
                db.add_all([
                    Message(conversation_id=session.conversation_id, role=MessageRole.USER, content=transcript, channel='voice'),
                    Message(conversation_id=session.conversation_id, role=MessageRole.ASSISTANT, content=spoken, channel='voice'),
                ])
                await db.commit()
        await _speak(session, voice, spoken)
        session.awaiting_agent_reply = False
        marks = session.marks
        if reservation_confirmed and session.websocket and marks is not None:
            mark = marks.generated()
            session.hangup_after_mark = mark
            marks.sent(mark)
            try:
                await session.websocket.send_json({'event': 'mark', 'mark': {'name': mark}})
            except Exception:
                session.hangup_after_mark = None
                raise
        if proposal_id and session.websocket and not session.closed:
            mark = session.marks.generated()
            session.presentation_mark = (mark, proposal_id)
            session.marks.sent(mark)
            await session.websocket.send_json({'event': 'mark', 'mark': {'name': mark}})
    session.agent_state = "listening"
    timeline.record(session, "agent.state", {"state": "listening"})


async def _publish_agent_event(
    session: CallSession,
    kind: str,
    payload: dict[str, Any],
    answer: list[str],
    *,
    customer_turn_offset_ms: int,
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
    if kind == "agent.signals":
        # Signals describe the customer message that started this turn. Keeping
        # that original offset makes replay and seeking update on the message,
        # even though classification finishes a little later in real time.
        timeline.record(session, kind, payload, at_offset_ms=customer_turn_offset_ms)
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
    announced = False
    try:
        while True:
            chunk = await chunks.get()
            if chunk is None or session.closed:
                break
            if not announced:
                announced = True
                at = _agent_heard_ms(session)
                timeline.record(session, "transcript.final", {"speaker": "agent", "text": text}, at_offset_ms=at)
                session.agent_state = "speaking"
                timeline.record(session, "agent.state", {"state": "speaking"}, at_offset_ms=at)
            canonical = resample_pcm16le(chunk, source_rate, CANONICAL_RATE)
            await emit_agent_audio(session, canonical)
        if text.strip() and not announced and not session.closed:
            timeline.record(session, "transcript.final", {"speaker": "agent", "text": text})
    except asyncio.CancelledError:
        await _cancel_playback(session)
        raise
    finally:
        session.agent_segment_open = False
        producer.cancel()


def _agent_heard_ms(session: CallSession) -> int:
    customer_samples = len(session.customer_pcm) // 2
    agent_samples = len(session.agent_pcm) // 2
    start = max(customer_samples, agent_samples) if not session.agent_segment_open else agent_samples
    return timeline_ms(session.recording_offset_ms, start)


async def emit_agent_audio(session: CallSession, pcm: bytes) -> None:
    if not pcm:
        return
    # A tentative barge holds the reply instead of discarding it: if the customer
    # really speaks the turn is cancelled, otherwise playback resumes here.
    pause = session.barge_pause
    if pause is not None and pause.is_set():
        await pause.wait()
        if session.closed:
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
