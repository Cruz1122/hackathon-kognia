from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..agent.tools.contracts import ToolContext
from ..features.agent.service import stream_agent
from ..platform.tracing import TraceRecorder, save_trace
from .audio import CANONICAL_RATE, pcm16le_to_wire, resample_pcm16le, timeline_ms
from .frames import CHANNEL_AGENT, encode_audio_frame
from .live_audio import live_audio_hub
from .marks import MarkTracker
from .sessions import CallSession
from .timeline import timeline
from .tts import PiperTTSProvider

logger = logging.getLogger("hackathon.telnyx.bridge")

BACKCHANNEL_DELAY_SECONDS = 0.7
PCM_CHUNK_BYTES = 6400


class AudioPlaybackCoordinator:
    """Keep one voice output active while the agent keeps processing."""

    def __init__(self, session: CallSession, voice: PiperTTSProvider) -> None:
        self.session = session
        self.voice = voice
        self._lock = asyncio.Lock()
        self._guard_ready = asyncio.Event()
        self._work_ready = asyncio.Event()
        self._final_ready = asyncio.Event()
        self._allow_backchannel = True
        self._backchannel_task: asyncio.Task[None] | None = None

    def set_agent_guard(self, payload: dict[str, Any]) -> None:
        """Accept the runtime's structural preflight before playing audio."""
        self._allow_backchannel = bool(payload.get("allow_backchannel", True))
        stage = payload.get("stage")
        if stage is None or stage == "buscando":
            self._work_ready.set()
        self._guard_ready.set()

    def mark_work_started(self) -> None:
        self._work_ready.set()

    def schedule_backchannel(self, text: str) -> None:
        self._backchannel_task = asyncio.create_task(self._play_backchannel(text))
        timeline.record(
            self.session,
            "audio.backchannel.scheduled",
            {"delay_ms": int(BACKCHANNEL_DELAY_SECONDS * 1000)},
        )

    async def _play_backchannel(self, text: str) -> None:
        try:
            await asyncio.sleep(BACKCHANNEL_DELAY_SECONDS)
            await self._guard_ready.wait()
            if self._final_ready.is_set() or not self._allow_backchannel or self.session.closed:
                return
            if not self._work_ready.is_set():
                return
            async with self._lock:
                if self._final_ready.is_set() or self.session.closed:
                    return
                await _speak_raw(self.session, self.voice, text, audio_kind="backchannel")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Backchannel playback failed")

    async def play_final(self, text: str) -> None:
        self._final_ready.set()
        await self.cancel_backchannel()
        async with self._lock:
            await _speak_raw(self.session, self.voice, text, audio_kind="final")

    async def cancel_backchannel(self) -> None:
        self._final_ready.set()
        task = self._backchannel_task
        self._backchannel_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _with_holding(generator, session: CallSession, voice):
    """Relay the stateful generator without blocking it on TTS."""
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
            event = await queue.get()
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
    from ..agent.phrases import BACKCHANNEL, pick

    session.voice_turn_started_at = time.monotonic()
    session.voice_first_audio_ms = None
    session.voice_useful_answer_ms = None
    coordinator = AudioPlaybackCoordinator(session, voice)
    session.playback_coordinator = coordinator
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
    recorder = TraceRecorder(
        call_id=str(session.call_id),
        conversation_id=str(session.conversation_id) if session.conversation_id else None,
        organization_id=str(session.organization_id) if session.organization_id else None,
    )
    # Policy-only turns do not reach the model generator.  Initialize the
    # recorder at the transport boundary so those turns still leave a root
    # span and appear in the technical replay.
    recorder.start(transcript, list(session.history), [])
    agent_kwargs: dict[str, Any] = {
        "messages": list(session.history),
        "tool_context": tool_context,
    }
    if "trace" in inspect.signature(agent).parameters:
        agent_kwargs["trace"] = recorder
    generator = agent(transcript, **agent_kwargs)
    coordinator.schedule_backchannel(pick(BACKCHANNEL))
    proposal_id = None
    last_done: dict[str, Any] = {}
    trace_status = 'ok'
    reservation_confirmed = False
    session.awaiting_agent_reply = True
    waiting = _with_holding(generator, session, voice)
    try:
        async for kind, payload in waiting:
            if session.closed:
                break
            if kind == 'done':
                last_done = payload
                proposal_id = payload.get('proposal_id')
            if (
                kind == 'tool.completed'
                and payload.get('tool') == 'create_booking'
                and payload.get('ok') is True
            ):
                reservation_confirmed = True
            await _publish_agent_event(
                session,
                kind,
                payload,
                answer,
                customer_turn_offset_ms=customer_turn_offset_ms,
            )
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
            await coordinator.play_final(spoken)
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
    except asyncio.CancelledError:
        trace_status = 'cancelled'
        await _cancel_playback(session)
        raise
    except Exception:
        trace_status = 'error'
        raise
    finally:
        await waiting.aclose()
        await generator.aclose()
        await coordinator.cancel_backchannel()
        recorder.record_voice_timing(
            first_audio_ms=session.voice_first_audio_ms,
            useful_answer_ms=session.voice_useful_answer_ms,
        )
        recorder.finish(
            answer=''.join(answer),
            provider=last_done.get('provider'),
            model=last_done.get('model'),
            status=trace_status,
        )
        await save_trace(recorder)


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
        coordinator = session.playback_coordinator
        if coordinator is not None:
            coordinator.mark_work_started()
        timeline.record(session, "tool.started", payload)
        return
    if kind == "tool.completed":
        timeline.record(session, "tool.completed", payload)
        return
    if kind in {"rag.started", "rag.completed"}:
        coordinator = session.playback_coordinator
        if coordinator is not None and kind == "rag.started":
            coordinator.mark_work_started()
        timeline.record(session, kind, payload)
        return
    if kind == "agent.guard":
        coordinator = session.playback_coordinator
        if coordinator is not None:
            coordinator.set_agent_guard(payload)
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


async def _speak_raw(
    session: CallSession,
    voice: PiperTTSProvider,
    text: str,
    *,
    audio_kind: str = "final",
) -> None:
    cached = voice.cached_audio(text) if audio_kind == "backchannel" and hasattr(voice, "cached_audio") else None
    if audio_kind == "backchannel" and cached is None:
        # Backchannels are intentionally best-effort. Never synthesize one on
        # the call's critical path when the warm cache is unavailable.
        return
    if cached is not None:
        source_rate, pcm = cached
        cached_chunks = (pcm[index:index + PCM_CHUNK_BYTES] for index in range(0, len(pcm), PCM_CHUNK_BYTES))
        producer = None
    else:
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
        cached_chunks = None

    announced = False

    async def emit_chunk(chunk: bytes) -> None:
        nonlocal announced
        if not announced:
            announced = True
            at = _agent_heard_ms(session)
            timeline.record(
                session,
                "transcript.final",
                {"speaker": "agent", "text": text, "audio_kind": audio_kind},
                at_offset_ms=at,
            )
            session.agent_state = "speaking"
            timeline.record(session, "agent.state", {"state": "speaking"}, at_offset_ms=at)
            if session.voice_turn_started_at:
                elapsed_ms = int((time.monotonic() - session.voice_turn_started_at) * 1000)
                if session.voice_first_audio_ms is None:
                    session.voice_first_audio_ms = elapsed_ms
                    timeline.record(session, "audio.first_audio", {"kind": audio_kind, "latency_ms": elapsed_ms})
                if audio_kind == "final" and session.voice_useful_answer_ms is None:
                    session.voice_useful_answer_ms = elapsed_ms
                    timeline.record(session, "audio.useful_answer", {"latency_ms": elapsed_ms})
        canonical = resample_pcm16le(chunk, source_rate, CANONICAL_RATE)
        await emit_agent_audio(session, canonical, audio_kind=audio_kind)

    try:
        if cached_chunks is not None:
            for chunk in cached_chunks:
                if session.closed:
                    break
                await emit_chunk(chunk)
        else:
            while True:
                chunk = await chunks.get()
                if chunk is None or session.closed:
                    break
                await emit_chunk(chunk)
        if text.strip() and not announced and not session.closed:
            timeline.record(session, "transcript.final", {"speaker": "agent", "text": text, "audio_kind": audio_kind})
    except asyncio.CancelledError:
        await _cancel_playback(session)
        raise
    finally:
        session.agent_segment_open = False
        if producer is not None:
            producer.cancel()


async def _speak(session: CallSession, voice: PiperTTSProvider, text: str) -> None:
    """Compatibility wrapper for greetings and silence prompts."""
    await _speak_raw(session, voice, text, audio_kind="final")


def _agent_heard_ms(session: CallSession) -> int:
    customer_samples = len(session.customer_pcm) // 2
    agent_samples = len(session.agent_pcm) // 2
    start = max(customer_samples, agent_samples) if not session.agent_segment_open else agent_samples
    return timeline_ms(session.recording_offset_ms, start)


def agent_heard_ms(session: CallSession) -> int:
    return _agent_heard_ms(session)


def catch_up_customer_clock(session: CallSession) -> None:
    """Fill caller silence up to the session clock so a late mic does not overlap the greeting."""
    elapsed_ms = max(0, session.offset_ms() - session.recording_offset_ms)
    target = int(elapsed_ms * CANONICAL_RATE / 1000)
    current = len(session.customer_pcm) // 2
    if target > current:
        session.customer_pcm.extend(b"\x00\x00" * (target - current))


def place_customer_pcm(session: CallSession, pcm: bytes, sample_rate: int) -> None:
    """Append caller audio on the recording clock, padding any gap since the previous frame."""
    if not pcm:
        return
    canonical = pcm if sample_rate == CANONICAL_RATE else resample_pcm16le(pcm, sample_rate, CANONICAL_RATE)
    incoming = len(canonical) // 2
    if incoming <= 0:
        return
    elapsed_ms = max(0, session.offset_ms() - session.recording_offset_ms)
    elapsed = int(elapsed_ms * CANONICAL_RATE / 1000)
    current = len(session.customer_pcm) // 2
    end = max(elapsed, current + incoming)
    start = max(current, end - incoming)
    if start > current:
        session.customer_pcm.extend(b"\x00\x00" * (start - current))
    session.customer_pcm.extend(canonical)


def remember_agent_pcm(session: CallSession, pcm: bytes) -> tuple[int, int]:
    """Keep assistant audio on the caller clock. Returns the byte span just written."""
    if not pcm:
        end = len(session.agent_pcm)
        return end, end
    customer_samples = len(session.customer_pcm) // 2
    agent_samples = len(session.agent_pcm) // 2
    if not session.agent_segment_open and customer_samples > agent_samples:
        session.agent_pcm.extend(b"\x00\x00" * (customer_samples - agent_samples))
    session.agent_segment_open = True
    start = len(session.agent_pcm)
    session.agent_pcm.extend(pcm)
    return start, len(session.agent_pcm)


def mute_agent_from(session: CallSession, start: int) -> None:
    """Silence assistant audio that was queued but not played after a confirmed barge."""
    start = max(0, start)
    if start < len(session.agent_pcm):
        muted = bytearray(session.agent_pcm)
        muted[start:] = b"\x00" * (len(muted) - start)
        session.agent_pcm = muted
    session.agent_segment_open = False


async def emit_agent_audio(session: CallSession, pcm: bytes, *, audio_kind: str = "final") -> None:
    if not pcm:
        return
    # A tentative barge holds the reply instead of discarding it: if the customer
    # really speaks the turn is cancelled, otherwise playback resumes here.
    pause = session.barge_pause
    if pause is not None and pause.is_set():
        await pause.wait()
        if session.closed:
            return
    start, end = remember_agent_pcm(session, pcm)
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
    session.playback_spans.append((start, end, name))
    timeline.record(session, "audio.generated", {"name": name, "audio_kind": audio_kind})
    await websocket.send_json({"event": "mark", "mark": {"name": name}})
    session.marks.sent(name)
    timeline.record(session, "audio.sent", {"name": name, "audio_kind": audio_kind})


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
