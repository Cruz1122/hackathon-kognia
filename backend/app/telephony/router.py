from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..auth.tokens import InvalidTokenError, authenticate_token
from ..db.models import Call, CallEvent, Conversation, Customer, Recording, User
from ..db.session import get_db, get_session_factory
from .live_audio import live_audio_hub, monitor_hub
from .recording import public_recording, recording_store
from .runtime import runtime
from .sessions import registry
from .state import project_state

logger = logging.getLogger("hackathon.telnyx.http")
router = APIRouter()


async def authenticate(token: str) -> User:
    async with get_session_factory()() as session:
        return await authenticate_token(token, session)


@router.post("/webhooks/telnyx/voice")
async def telnyx_voice(request: Request) -> Any:
    raw = await request.body()
    status, body = await runtime.handle_webhook(
        raw,
        signature=request.headers.get("telnyx-signature-ed25519"),
        timestamp=request.headers.get("telnyx-timestamp"),
    )
    if status != 200:
        raise HTTPException(status_code=status, detail=body.get("detail"))
    return body


@router.websocket("/ws/telnyx/stream/{call_id}")
async def telnyx_stream(websocket: WebSocket, call_id: str, token: str = "") -> None:
    await runtime.handle_media(websocket, call_id, token)


def _owns(user: User, organization_id: uuid.UUID | None) -> bool:
    return user.organization_id is not None and organization_id == user.organization_id


def _stored_call(row: Call) -> dict[str, Any]:
    customer = row.conversation.customer if row.conversation is not None else None
    ended = row.ended_at
    duration_ms = int((ended - row.started_at).total_seconds() * 1000) if ended is not None else 0
    return {
        "id": str(row.id),
        "lifecycle": row.lifecycle_state,
        "status": str(row.status),
        "started_at": row.started_at.isoformat(),
        "ended_at": ended.isoformat() if ended is not None else None,
        "duration_ms": max(duration_ms, 0),
        "caller": customer.phone if customer is not None and customer.phone else "",
        "customer_name": customer.name if customer is not None and customer.name else "",
        "recording_offset_ms": row.recording_offset_ms,
        "replayable": bool(row.telnyx_call_control_id),
    }


def _listed(row: Call) -> bool:
    if row.telnyx_call_control_id:
        return True
    customer = row.conversation.customer if row.conversation is not None else None
    if customer is None:
        return False
    return bool((customer.name or "").strip() or (customer.phone or "").strip())


@router.get("/calls")
async def list_calls(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if user.organization_id is None:
        raise HTTPException(status_code=404, detail="not found")
    live_sessions = registry.list_for(user.organization_id)
    live_ids = {item.call_id for item in live_sessions}
    phones = {item.caller for item in live_sessions if item.caller}
    named: dict[str, str] = {}
    if phones:
        customers = (
            await session.scalars(
                select(Customer).where(
                    Customer.organization_id == user.organization_id,
                    Customer.phone.in_(phones),
                )
            )
        ).all()
        named = {customer.phone: customer.name or "" for customer in customers if customer.phone}
    rows = list(
        (
            await session.scalars(
                select(Call)
                .where(Call.organization_id == user.organization_id)
                .options(joinedload(Call.conversation).joinedload(Conversation.customer))
                .order_by(Call.started_at.desc())
                .limit(500)
            )
        ).unique().all()
    )
    recent = [_stored_call(row) for row in rows if row.id not in live_ids and _listed(row)]
    live = []
    for item in live_sessions:
        body = item.public_view()
        body["customer_name"] = named.get(item.caller, "")
        body["status"] = "active"
        body["replayable"] = True
        live.append(body)
    return {"calls": live, "recent": recent}


@router.get("/calls/{call_id}")
async def get_call(
    call_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    live = registry.get(call_id)
    if live is not None:
        if not _owns(user, live.organization_id):
            raise HTTPException(status_code=404, detail="not found")
        body = live.public_view()
        record = recording_store.get(call_id)
        if record is not None and record.organization_id == user.organization_id:
            body["recording"] = public_recording(record)
        return body
    row = await session.get(Call, call_id)
    if row is None or not _owns(user, row.organization_id):
        raise HTTPException(status_code=404, detail="not found")
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "conversation_id": str(row.conversation_id),
        "lifecycle": row.lifecycle_state,
        "started_at": row.started_at.isoformat(),
        "recording_offset_ms": row.recording_offset_ms,
        "duration_ms": 0,
    }


@router.get("/calls/{call_id}/timeline")
async def get_timeline(
    call_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_call(call_id, user, session)
    live = registry.get(call_id)
    if live is not None and live.events:
        events = live.events
    else:
        rows = list(
            (
                await session.scalars(
                    select(CallEvent)
                    .where(CallEvent.call_id == call_id, CallEvent.organization_id == user.organization_id)
                    .order_by(CallEvent.seq)
                )
            ).all()
        )
        events = [
            {
                "seq": row.seq,
                "type": row.event_type,
                "offset_ms": row.offset_ms,
                "occurred_at": row.occurred_at.isoformat(),
                "provider_occurred_at": row.provider_occurred_at.isoformat() if row.provider_occurred_at else None,
                "payload": row.payload or {},
            }
            for row in rows
        ]
    return {"call_id": str(call_id), "events": events}


@router.get("/calls/{call_id}/state")
async def get_state(
    call_id: uuid.UUID,
    at_ms: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    timeline_body = await get_timeline(call_id, user, session)
    return project_state(timeline_body["events"], at_ms)


@router.get("/calls/{call_id}/recording")
async def get_recording(
    call_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    await _require_call(call_id, user, session)
    record = recording_store.get(call_id)
    path = record.path if record is not None and record.organization_id == user.organization_id else None
    if path is None or not path.is_file():
        row = await session.scalar(
            select(Recording).where(Recording.call_id == call_id, Recording.organization_id == user.organization_id)
        )
        if row is None or row.status != "READY":
            raise HTTPException(status_code=404, detail="not found")
        from .recording import recording_path

        path = recording_path(row.id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{call_id}.wav")


async def _require_call(call_id: uuid.UUID, user: User, session: AsyncSession) -> None:
    live = registry.get(call_id)
    if live is not None:
        if not _owns(user, live.organization_id):
            raise HTTPException(status_code=404, detail="not found")
        return
    row = await session.get(Call, call_id)
    if row is None or not _owns(user, row.organization_id):
        raise HTTPException(status_code=404, detail="not found")


@router.websocket("/ws/calls/monitor")
async def monitor_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    organization_id: uuid.UUID | None = None
    subscribed: uuid.UUID | None = None
    queue: asyncio.Queue[dict[str, Any]] | None = None
    pump: asyncio.Task[None] | None = None
    try:
        auth_message = await websocket.receive_json()
        token = auth_message.get("token") if isinstance(auth_message, dict) else None
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth" or not isinstance(token, str):
            await websocket.close(code=4401)
            return
        try:
            user = await authenticate(token)
        except InvalidTokenError:
            await websocket.close(code=4401)
            return
        organization_id = user.organization_id
        if organization_id is None:
            await websocket.close(code=4403)
            return

        async def pump_events() -> None:
            nonlocal queue, subscribed
            assert queue is not None
            assert subscribed is not None
            seen_messages: set[str] = set()
            initial = registry.get(subscribed)
            assert initial is not None
            conversation_id = initial.conversation_id
            latest_started_at = initial.started_at
            last_poll = 0.0
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1)
                    await websocket.send_json({**message, 'call_id': str(subscribed)})
                except asyncio.TimeoutError:
                    pass
                if conversation_id is None:
                    continue
                now = asyncio.get_running_loop().time()
                if now - last_poll < 1:
                    continue
                last_poll = now
                # Durable polling also sees WhatsApp writes from the separate worker.
                from ..db.models import Message, MessageRole
                from ..db.session import get_session_factory
                async with get_session_factory()() as db:
                    rows = (await db.scalars(select(Message).join(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.organization_id == organization_id,
                        Message.channel.in_(['whatsapp', 'system'])
                    ).order_by(Message.created_at.desc(), Message.id.desc()).limit(100))).all()
                for row in reversed(rows):
                    key = str(row.id)
                    if key in seen_messages:
                        continue
                    seen_messages.add(key)
                    await websocket.send_json({'type': 'conversation.event' if row.channel == 'system' else 'transcript.final',
                        'message_id': key, 'payload': {'channel': 'whatsapp', 'text': row.content,
                        'speaker': 'customer' if row.role == MessageRole.USER else 'agent'}})
                candidates = [item for item in registry.list_for(organization_id)
                    if item.conversation_id == conversation_id
                    and item.started_at > latest_started_at]
                if candidates:
                    live = max(candidates, key=lambda item: item.started_at)
                    latest_started_at = live.started_at
                    monitor_hub.unsubscribe(organization_id, subscribed, queue)
                    subscribed = live.call_id
                    queue = monitor_hub.subscribe(organization_id, subscribed)
                    await websocket.send_json({'type': 'call.snapshot', 'call': live.public_view()})
                    for event in live.events:
                        await websocket.send_json({**event, 'call_id': str(subscribed)})

        while True:
            incoming = await websocket.receive_json()
            if not isinstance(incoming, dict):
                continue
            if incoming.get("type") == "unsubscribe":
                if pump is not None:
                    pump.cancel()
                    pump = None
                if queue is not None and subscribed is not None:
                    monitor_hub.unsubscribe(organization_id, subscribed, queue)
                queue = None
                subscribed = None
                continue
            if incoming.get("type") != "subscribe.call":
                continue
            try:
                call_id = uuid.UUID(str(incoming.get("call_id")))
            except ValueError:
                await websocket.send_json({"type": "error", "error": "not found"})
                continue
            live = registry.get(call_id)
            if live is None or live.organization_id != organization_id:
                await websocket.send_json({"type": "error", "error": "not found"})
                continue
            if pump is not None:
                pump.cancel()
            if queue is not None and subscribed is not None:
                monitor_hub.unsubscribe(organization_id, subscribed, queue)
            subscribed = call_id
            queue = monitor_hub.subscribe(organization_id, call_id)
            pump = asyncio.create_task(pump_events())
            await websocket.send_json({"type": "call.snapshot", "call": live.public_view()})
            for event in live.events:
                await websocket.send_json(
                    {"type": event["type"], "call_id": str(call_id), "seq": event["seq"], "offset_ms": event["offset_ms"], "payload": event["payload"]}
                )
    except WebSocketDisconnect:
        logger.info("Call monitor disconnected")
    except Exception:
        logger.exception("Call monitor failed")
    finally:
        if pump is not None:
            pump.cancel()
        if queue is not None and subscribed is not None and organization_id is not None:
            monitor_hub.unsubscribe(organization_id, subscribed, queue)


@router.websocket("/ws/calls/{call_id}/audio")
async def call_audio(websocket: WebSocket, call_id: str) -> None:
    await websocket.accept()
    viewer: asyncio.Queue[bytes] | None = None
    organization_id: uuid.UUID | None = None
    parsed: uuid.UUID | None = None
    try:
        auth_message = await websocket.receive_json()
        token = auth_message.get("token") if isinstance(auth_message, dict) else None
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth" or not isinstance(token, str):
            await websocket.close(code=4401)
            return
        try:
            user = await authenticate(token)
        except InvalidTokenError:
            await websocket.close(code=4401)
            return
        if user.organization_id is None:
            await websocket.close(code=4403)
            return
        parsed = uuid.UUID(call_id)
        live = registry.get(parsed)
        if live is None or live.organization_id != user.organization_id:
            await websocket.close(code=4403)
            return
        organization_id = user.organization_id
        viewer = live_audio_hub.subscribe(organization_id, parsed)
        while True:
            frame = await viewer.get()
            await websocket.send_bytes(frame)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Live audio socket failed")
    finally:
        if viewer is not None and organization_id is not None and parsed is not None:
            live_audio_hub.unsubscribe(organization_id, parsed, viewer)
