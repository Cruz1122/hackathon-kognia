from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .audio import mixed_call_wav, pcm16le_rms, resolve_byte_order, wire_to_pcm16le
from .bridge import run_agent_turn
from .frames import CHANNEL_CUSTOMER, encode_audio_frame
from .live_audio import live_audio_hub
from .marks import MarkTracker
from .recording import RecordingRecord, persist_recording, recording_store
from .sessions import CallSession, registry
from .settings import load_settings, telnyx_enabled
from .signatures import SignatureError, verify_telnyx_signature
from .stt import SherpaSTTProvider
from .telnyx_api import TelnyxApi
from .timeline import timeline

logger = logging.getLogger("hackathon.telnyx")
BARGE_RMS = 0.05
SPEECH_RMS = 0.02
SILENCE_SECONDS = 0.8
MAX_UTTERANCE_SECONDS = 8.0


def utterance_ready(
    *,
    last_voice_at: float,
    first_voice_at: float,
    now: float,
    endpoint: bool,
) -> bool:
    if endpoint:
        return True
    if last_voice_at > 0 and now - last_voice_at >= SILENCE_SECONDS:
        return True
    return first_voice_at > 0 and now - first_voice_at >= MAX_UTTERANCE_SECONDS


def usable_transcript(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 2


def _party(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("phone_number") or value.get("number") or "")
    return str(value or "")


class TelephonyRuntime:
    def __init__(self) -> None:
        self.transport: Any = None
        self.enable_voice = False
        self.stt_factory: type[SherpaSTTProvider] = SherpaSTTProvider
        self.agent: Any = None
        self._pending_recordings: dict[str, str] = {}

    def api(self) -> TelnyxApi:
        return TelnyxApi(load_settings(), self.transport)

    async def handle_webhook(
        self,
        raw_body: bytes,
        *,
        signature: str | None,
        timestamp: str | None,
    ) -> tuple[int, dict[str, Any]]:
        if not telnyx_enabled():
            return 404, {"detail": "disabled"}
        settings = load_settings()
        try:
            verify_telnyx_signature(
                raw_body=raw_body,
                signature=signature,
                timestamp=timestamp,
                public_key=settings.public_key,
                window_seconds=settings.replay_window_seconds,
            )
        except SignatureError:
            return 403, {"detail": "invalid signature"}
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return 400, {"detail": "invalid json"}
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            return 400, {"detail": "missing event id"}
        event_id = str(data["id"])
        if not registry.claim_event(event_id):
            return 200, {"ok": True, "duplicate": True}
        try:
            await self._dispatch(data)
        except Exception:
            registry.release_event(event_id)
            logger.exception("Telnyx webhook failed")
            return 500, {"detail": "webhook failed"}
        return 200, {"ok": True}

    async def _dispatch(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("event_type") or "")
        body = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        control_id = str(body.get("call_control_id") or "")
        occurred_at = data.get("occurred_at")
        if event_type == "call.initiated" and str(body.get("direction") or "incoming") in {"incoming", "inbound"}:
            await self._answer_inbound(body, occurred_at if isinstance(occurred_at, str) else None)
            return
        session = registry.by_control(control_id) if control_id else None
        if event_type == "call.hangup" and session is not None:
            await self.finish_call(session, occurred_at if isinstance(occurred_at, str) else None)
            return
        if event_type == "call.recording.saved":
            await self._recording_saved(session, control_id, body)
            return
        if event_type == "call.recording.error":
            if session is not None:
                record = recording_store.get(session.call_id)
                if record is not None:
                    record.status = "ERROR"
                    record.error = "recording error"
                timeline.record(session, "recording.error", {"state": "ERROR"})
            return

    async def _answer_inbound(self, body: dict[str, Any], occurred_at: str | None) -> None:
        control_id = str(body.get("call_control_id") or "")
        session = registry.create(
            telnyx_call_control_id=control_id,
            call_leg_id=str(body.get("call_leg_id") or "") or None,
            call_session_id=str(body.get("call_session_id") or "") or None,
            caller=_party(body.get("from")),
            callee=_party(body.get("to")),
        )
        await self._bind_tenant(session)
        await self._persist_call(session)
        if session.answered:
            return
        session.answered = True
        api = self.api()
        media_url = api.media_url(str(session.call_id), session.token)
        await api.answer(control_id)
        await api.streaming_start(control_id, media_url)
        session.lifecycle_state = "ACTIVE"
        timeline.record(session, "lifecycle", {"state": "ACTIVE"}, provider_occurred_at=occurred_at)
        session.recording_offset_ms = session.last_offset_ms
        try:
            await api.record_start(control_id)
            self._ensure_recording(session, None, "RECORDING")
        except Exception:
            logger.exception("Telnyx record_start failed")
            timeline.record(session, "recording.error", {"state": "ERROR"})
        pending = self._pending_recordings.pop(control_id, None)
        if pending:
            await self._recording_saved(session, control_id, {"recording_urls": {"wav": pending}})

    async def _bind_tenant(self, session: CallSession) -> None:
        settings = load_settings()
        if settings.organization_id and settings.system_user_id:
            session.organization_id = uuid.UUID(settings.organization_id)
            session.system_user_id = uuid.UUID(settings.system_user_id)
            return
        try:
            from sqlalchemy import select

            from ..db.models import Organization, User, UserRole
            from ..db.session import get_session_factory

            async with get_session_factory()() as db:
                organizations = list((await db.scalars(select(Organization))).all())
                preferred = os.getenv("DEMO_ORG_SLUG", "demo-kognia").strip() or "demo-kognia"
                chosen = next((item for item in organizations if item.slug == preferred), None)
                if chosen is None and len(organizations) == 1:
                    chosen = organizations[0]
                if chosen is None:
                    logger.error("Telnyx tenant is ambiguous")
                    return
                admin = await db.scalar(
                    select(User).where(
                        User.organization_id == chosen.id,
                        User.role == UserRole.ADMIN,
                    )
                )
                if admin is None:
                    return
                session.organization_id = chosen.id
                session.system_user_id = admin.id
                _remember_tenant(chosen.id, admin.id)
        except Exception:
            logger.exception("Telnyx tenant lookup failed")

    async def _persist_call(self, session: CallSession) -> None:
        if session.organization_id is None or session.system_user_id is None:
            return
        try:
            from ..db.models import Call, CallStatus
            from ..db.queries import create_conversation
            from ..db.session import get_session_factory

            async with get_session_factory()() as db:
                conversation = await create_conversation(
                    db,
                    organization_id=session.organization_id,
                    created_by=session.system_user_id,
                    channel="pstn",
                    status="open",
                )
                db.add(
                    Call(
                        id=session.call_id,
                        organization_id=session.organization_id,
                        conversation_id=conversation.id,
                        status=CallStatus.ACTIVE,
                        telnyx_call_control_id=session.telnyx_call_control_id,
                        call_leg_id=session.call_leg_id,
                        call_session_id=session.call_session_id,
                        lifecycle_state="ACTIVE",
                        external_id=session.telnyx_call_control_id,
                    )
                )
                await db.commit()
                session.conversation_id = conversation.id
        except Exception:
            logger.exception("Telnyx call persistence failed")

    def _ensure_recording(self, session: CallSession, download_url: str | None, status: str) -> RecordingRecord | None:
        if session.organization_id is None:
            return None
        current = recording_store.get(session.call_id)
        if current is None:
            current = recording_store.upsert(
                RecordingRecord(
                    id=uuid.uuid4(),
                    call_id=session.call_id,
                    organization_id=session.organization_id,
                    status=status,
                    download_url=download_url,
                )
            )
        elif download_url and not current.download_url:
            current.download_url = download_url
        current.status = status
        return current

    async def _recording_saved(self, session: CallSession | None, control_id: str, body: dict[str, Any]) -> None:
        urls = body.get("recording_urls") if isinstance(body.get("recording_urls"), dict) else {}
        public = body.get("public_recording_urls") if isinstance(body.get("public_recording_urls"), dict) else {}
        download_url = str(urls.get("wav") or public.get("wav") or urls.get("mp3") or "")
        if session is None:
            if download_url:
                self._pending_recordings[control_id] = download_url
            return
        record = self._ensure_recording(session, download_url or None, "PROCESSING")
        timeline.record(session, "recording.saved", {"state": "PROCESSING"})
        if record is None:
            return
        await persist_recording(record)
        if session.organization_id and session.conversation_id and record.download_url:
            from ..platform.queue import enqueue_recording

            await enqueue_recording(session.organization_id, session.conversation_id, record.id)

    async def finish_call(self, session: CallSession, occurred_at: str | None) -> None:
        task = session.turn_task
        if task is not None and not task.done():
            task.cancel()
        if session.stt is not None:
            session.stt.close()
            session.stt = None
        self.write_capture(session)
        session.lifecycle_state = "ENDED"
        timeline.record(session, "lifecycle", {"state": "ENDED"}, provider_occurred_at=occurred_at)
        registry.end(session)
        await self._close_db_call(session)

    async def _close_db_call(self, session: CallSession) -> None:
        if session.organization_id is None or session.conversation_id is None:
            return
        try:
            from sqlalchemy import update

            from ..db.models import Call, CallStatus
            from ..db.session import get_session_factory
            from ..platform.queue import enqueue_enrichment

            async with get_session_factory()() as db:
                await db.execute(
                    update(Call)
                    .where(Call.id == session.call_id)
                    .values(status=CallStatus.ENDED, ended_at=datetime.now(UTC), lifecycle_state="ENDED")
                )
                await db.commit()
            await enqueue_enrichment(session.organization_id, session.conversation_id)
        except Exception:
            logger.exception("Telnyx hangup persistence failed")

    def write_capture(self, session: CallSession) -> Path | None:
        if not session.customer_pcm and not session.agent_pcm:
            return None
        settings = load_settings()
        try:
            settings.capture_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        target = settings.capture_dir / f"{session.call_id}.wav"
        customer = bytes(session.customer_pcm)
        agent = bytes(session.agent_pcm)
        target.write_bytes(mixed_call_wav(customer, agent))
        media_format = dict(session.media_format)
        media_format["resolved_byte_order"] = resolve_byte_order(media_format) if media_format else "little"
        (settings.capture_dir / f"{session.call_id}.json").write_text(
            json.dumps({"media_format": media_format, "pcm_bytes": len(session.customer_pcm)}),
            encoding="utf-8",
        )
        return target

    async def handle_media(self, websocket: WebSocket, call_id: str, token: str) -> None:
        await websocket.accept()
        try:
            parsed = uuid.UUID(call_id)
        except ValueError:
            await websocket.close(code=1008)
            return
        session = registry.get(parsed)
        if session is None or not token or not registry.token_matches(session, token):
            await websocket.close(code=1008)
            return
        session.websocket = websocket
        if session.marks is None:
            session.marks = MarkTracker()
        if self.enable_voice and session.stt is None:
            try:
                session.stt = self.stt_factory().clone()
            except Exception:
                logger.exception("Sherpa stream setup failed")
                session.stt = None
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._on_media_event(session, event)
        finally:
            if session.websocket is websocket:
                session.websocket = None

    async def _on_media_event(self, session: CallSession, event: dict[str, Any]) -> None:
        kind = str(event.get("event") or "")
        if kind == "start":
            start = event.get("start") if isinstance(event.get("start"), dict) else {}
            media_format = start.get("media_format") if isinstance(start.get("media_format"), dict) else {}
            session.media_format = dict(media_format)
            timeline.record(
                session,
                "media.started",
                {
                    "encoding": media_format.get("encoding"),
                    "sample_rate": media_format.get("sample_rate"),
                    "channels": media_format.get("channels"),
                    "byte_order": resolve_byte_order(media_format),
                },
            )
            return
        if kind == "media":
            media = event.get("media") if isinstance(event.get("media"), dict) else {}
            track = str(media.get("track") or "inbound")
            if track not in {"inbound", "inbound_track"}:
                return
            encoded = str(media.get("payload") or "")
            if not encoded:
                return
            pcm = wire_to_pcm16le(base64.b64decode(encoded), session.media_format or {"encoding": "L16", "sample_rate": 16000})
            await self._on_customer_pcm(session, pcm)
            return
        if kind == "mark" and session.marks is not None:
            mark = event.get("mark") if isinstance(event.get("mark"), dict) else {}
            name = str(mark.get("name") or "")
            if session.marks.played(name) == "played":
                timeline.record(session, "audio.played", {"name": name})
            return
        if kind == "stop":
            self.write_capture(session)

    async def _on_customer_pcm(self, session: CallSession, pcm: bytes) -> None:
        session.customer_pcm.extend(pcm)
        if session.organization_id is not None:
            live_audio_hub.publish(
                session.organization_id,
                session.call_id,
                encode_audio_frame(
                    channel=CHANNEL_CUSTOMER,
                    flags=0,
                    seq=session.sequence,
                    offset_ms=session.offset_ms(),
                    pcm=pcm,
                ),
            )
        if session.agent_state == "speaking" and pcm16le_rms(pcm) >= BARGE_RMS:
            await self._barge_in(session)
        if session.stt is None:
            return
        busy = session.turn_task is not None and not session.turn_task.done()
        if busy:
            return
        now = time.monotonic()
        if pcm16le_rms(pcm) >= SPEECH_RMS:
            session.last_voice_at = now
            if session.first_voice_at <= 0:
                session.first_voice_at = now
        text, endpoint = await session.stt.feed(pcm)
        if text:
            timeline.record(session, "transcript.partial", {"speaker": "customer", "text": text}, persist=False)
        if utterance_ready(
            last_voice_at=session.last_voice_at,
            first_voice_at=session.first_voice_at,
            now=now,
            endpoint=endpoint,
        ):
            final = (await session.stt.finish()).strip() or text.strip()
            session.last_voice_at = 0.0
            session.first_voice_at = 0.0
            session.stt.close()
            session.stt = self.stt_factory().clone() if self.enable_voice else None
            if usable_transcript(final):
                logger.info("Telnyx turn started")
                timeline.record(session, "transcript.final", {"speaker": "customer", "text": final})
                await self._start_turn(session, final)

    async def _barge_in(self, session: CallSession) -> None:
        cancelled = session.marks.clear_unplayed() if session.marks is not None else []
        for name in cancelled:
            timeline.record(session, "audio.cancelled", {"name": name})
        if session.websocket is not None:
            try:
                await session.websocket.send_json({"event": "clear"})
            except Exception:
                logger.info("Telnyx clear failed")
        task = session.turn_task
        if task is not None and not task.done():
            task.cancel()
        session.agent_state = "listening"

    async def _start_turn(self, session: CallSession, transcript: str) -> None:
        current = session.turn_task
        if current is not None and not current.done():
            current.cancel()

        async def _guarded() -> None:
            try:
                await run_agent_turn(session, transcript, agent=self._agent())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telnyx agent turn failed")
                timeline.record(session, "agent.error", {"message": "turn failed"})

        session.turn_task = asyncio.create_task(_guarded())

    def _agent(self) -> Any:
        if self.agent is not None:
            return self.agent
        from ..features.agent.service import stream_agent

        return stream_agent

    async def sync_webhook(self) -> None:
        if not telnyx_enabled():
            return
        try:
            await self.api().sync_webhook()
        except Exception:
            logger.exception("Telnyx webhook sync failed")


runtime = TelephonyRuntime()


def _remember_tenant(organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
    os.environ["TELNYX_ORGANIZATION_ID"] = str(organization_id)
    os.environ["TELNYX_SYSTEM_USER_ID"] = str(user_id)
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
        lines = []
        if "TELNYX_ORGANIZATION_ID=" not in existing:
            lines.append(f'TELNYX_ORGANIZATION_ID="{organization_id}"')
        if "TELNYX_SYSTEM_USER_ID=" not in existing:
            lines.append(f'TELNYX_SYSTEM_USER_ID="{user_id}"')
        if lines:
            with env_path.open("a", encoding="utf-8") as handle:
                handle.write("\n" + "\n".join(lines) + "\n")
    except OSError:
        logger.info("Telnyx tenant kept in memory")


def warm_voice_pipeline() -> None:
    """Run one Sherpa pass and one Piper phrase so the first call does not pay that cost."""
    from ..features.synthesis import service as piper
    from ..features.transcription import service as sherpa

    started = time.perf_counter()
    sherpa.preload_model()
    stream = sherpa.create_stream()
    sherpa.feed_pcm(stream, b"\x00\x00" * 16000, 16000)
    sherpa.finish_stream(stream, 16000)
    piper.preload_tts()
    for _chunk in piper.stream_tts_audio("Hola."):
        pass
    from ..platform.rag.runtime import embeddings as rag_embeddings

    rag_embeddings.preload()
    rag_embeddings.embed_queries(["hola"])
    logger.warning("Voice pipeline warmed in %.1fs", time.perf_counter() - started)


async def start_telephony() -> None:
    await timeline.start()
    if telnyx_enabled():
        runtime.enable_voice = True
        try:
            await asyncio.to_thread(warm_voice_pipeline)
        except Exception:
            logger.exception("Voice warmup failed")
        await runtime.sync_webhook()


async def stop_telephony() -> None:
    runtime.enable_voice = False
    await timeline.stop()
