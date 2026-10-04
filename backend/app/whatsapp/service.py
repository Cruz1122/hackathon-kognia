from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
import httpx

from ..agent.settings import settings
from ..agent.store import advisory_lock
from ..agent.tools.contracts import ToolContext
from ..db.models import AgentSnapshot, ChannelBinding, ChannelEvent, Conversation, Message, MessageRole
from ..db.session import get_session_factory
from .client import WhatsAppClient

logger = logging.getLogger(__name__)
STATUS_RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def event_key(*parts: str) -> str:
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


def parse_events(payload: dict, number: str):
    if payload.get('object') != 'whatsapp_business_account':
        raise ValueError('Unexpected object')
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value', {})
            if change.get('field') != 'messages' or value.get('metadata', {}).get('phone_number_id') != number:
                continue
            for message in value.get('messages', []):
                if not all(isinstance(message.get(k), str) and message[k] for k in ('id', 'from', 'timestamp', 'type')):
                    raise ValueError('Invalid message')
                # Meta may include a signed media URL; retain only the stable media ID.
                if message.get('type') == 'audio':
                    message = {**message, 'audio': {k: v for k, v in message.get('audio', {}).items() if k != 'url'}}
                yield 'inbound', message['from'], event_key(number, message['id']), message
            for status in value.get('statuses', []):
                if not all(isinstance(status.get(k), str) and status[k] for k in ('id', 'recipient_id', 'timestamp', 'status')):
                    raise ValueError('Invalid status')
                yield 'status', status['recipient_id'], event_key(number, status['id'], status['status'], status['timestamp']), status


async def receive(payload: dict) -> None:
    number = settings().whatsapp_number
    events = list(parse_events(payload, number))
    async with get_session_factory()() as db:
        for kind, wa_id, key, data in events:
            binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.phone_number_id == number, ChannelBinding.wa_id == wa_id))
            if binding is None:
                # Demo deliberately requires operator-verified identity; never guess tenant.
                logger.warning('Ignoring WhatsApp event without a verified binding')
                continue
            timestamp = datetime.fromtimestamp(int(data['timestamp']), UTC)
            if timestamp > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError('Future timestamp')
            if kind == 'inbound':
                # SQL max prevents concurrent webhooks moving the window backwards.
                from sqlalchemy import update, or_
                await db.execute(update(ChannelBinding).where(ChannelBinding.id == binding.id,
                    or_(ChannelBinding.last_inbound_at.is_(None), ChannelBinding.last_inbound_at < timestamp)
                ).values(last_inbound_at=timestamp))
            await db.execute(insert(ChannelEvent).values(id=key, binding_id=binding.id,
                kind=kind, payload=data, status='pending', attempts=0,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC)).on_conflict_do_nothing(index_elements=['id']))
        await db.commit()
    # The worker also scans durable pending rows, covering enqueue failures/crashes.
    if events:
        from ..platform.queue import enqueue_channel_work
        await enqueue_channel_work()


def outbound_payload(binding: ChannelBinding, text: str, now: datetime) -> dict:
    last = binding.last_inbound_at
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last and timedelta(0) <= now - last < timedelta(hours=24):
        return {'to': binding.wa_id, 'type': 'text', 'text': {'body': text[:4000]}}
    if not binding.opt_in or not binding.template_name:
        raise ValueError('An approved template and opt-in are required outside the service window')
    return {'to': binding.wa_id, 'type': 'template', 'template': {
        'name': binding.template_name, 'language': {'code': binding.template_language}}}


async def prepare_continuation(conversation_id: uuid.UUID, organization_id: uuid.UUID, source_id: str):
    async with get_session_factory()() as db:
        binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.conversation_id == conversation_id,
            ChannelBinding.organization_id == organization_id, ChannelBinding.phone_number_id == settings().whatsapp_number))
        if not binding or not binding.opt_in:
            return
        key = event_key('continuation', source_id)
        await db.execute(insert(ChannelEvent).values(id=key, binding_id=binding.id, kind='outbound',
            payload={'text': 'Podemos continuar la conversación que teníamos por llamada. ¿Deseas seguir?'},
            status='pending', attempts=0, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
        ).on_conflict_do_nothing(index_elements=['id']))
        await db.commit()


async def process_pending(limit: int = 20) -> None:
    if not settings().whatsapp_enabled:
        return
    async with get_session_factory()() as db:
        ids = list((await db.scalars(select(ChannelEvent.id).where(
            ChannelEvent.status.in_(['pending', 'processing', 'sending']),
            ChannelEvent.updated_at < datetime.now(UTC) - timedelta(seconds=2)
        ).order_by(ChannelEvent.created_at, ChannelEvent.id).limit(limit))).all())
    for key in ids:
        try:
            await process_event(key)
        except Exception as exc:
            logger.warning('Channel job failed: %s', type(exc).__name__)


async def process_event(key: str, client: WhatsAppClient | None = None) -> None:
    client = client or WhatsAppClient()
    # Cross-process exclusion; a crashed worker releases its PostgreSQL connection.
    async with advisory_lock(f'channel-event:{key}'):
        async with get_session_factory()() as db:
            event = await db.get(ChannelEvent, key)
            if not event or event.status not in {'pending', 'processing', 'sending'}:
                return
            binding = await db.get(ChannelBinding, event.binding_id)
            if binding is None:
                return
            if event.kind == 'status':
                matches = (await db.scalars(select(ChannelEvent).where(
                    ChannelEvent.binding_id == binding.id, ChannelEvent.kind == 'outbound',
                    ChannelEvent.external_id == event.payload['id']).with_for_update())).all()
                if not matches:
                    event.attempts += 1
                    event.status = 'ignored' if event.attempts >= 10 else 'pending'
                else:
                    for output in matches:
                        incoming = event.payload['status']
                        if (STATUS_RANK.get(incoming, 0) > STATUS_RANK.get(output.status, 0)
                                or incoming == 'failed' and output.status not in {'delivered', 'read'}):
                            output.status = incoming
                        if incoming in {'delivered', 'read'} and output.payload.get('proposal'):
                            from ..agent.store import mark_presented
                            await mark_presented(str(binding.organization_id), str(binding.conversation_id), output.payload['proposal'])
                    event.status = 'done'
                event.updated_at = datetime.now(UTC)
                await db.commit()
                return
            if event.kind == 'outbound':
                if event.status == 'sending':
                    event.status = 'uncertain'  # Never blindly repeat an externally accepted send.
                    await db.commit()
                    return
                try:
                    payload = outbound_payload(binding, event.payload['text'], datetime.now(UTC))
                except ValueError:
                    event.status = 'blocked'
                    await db.commit()
                    return
                event.status = 'sending'
                event.attempts += 1
                await db.commit()
                try:
                    external_id = await client.send(payload)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 and event.attempts < 3:
                        event.status = 'pending'
                    else:
                        event.status = 'failed' if exc.response.status_code < 500 else 'uncertain'
                except Exception:
                    event.status = 'uncertain'
                else:
                    event.external_id = external_id
                    event.status = 'accepted'
                event.updated_at = datetime.now(UTC)
                await db.commit()
                return
            event.status = 'processing'
            event.attempts += 1
            event.updated_at = datetime.now(UTC)
            await db.commit()
            try:
                text = event.payload.get('_transcript')
                if text is None:
                    text = await normalize_message(event.payload, client)
                    event.payload = {**event.payload, '_transcript': text}
                    await db.commit()
                conversation = await db.get(Conversation, binding.conversation_id)
                if conversation is None:
                    raise ValueError('Conversation not found')
                from ..features.agent.service import stream_agent
                response = ''
                async for kind, data in stream_agent(text, tool_context=ToolContext(
                    request_id=key, organization_id=str(binding.organization_id),
                    conversation_id=str(binding.conversation_id), user_id=str(conversation.created_by), channel='whatsapp')):
                    if kind == 'error':
                        raise RuntimeError('Agent turn failed')
                    if kind == 'token':
                        response += data['text']
                if not response:
                    raise RuntimeError('Empty response')
            except Exception:
                event.status = 'failed' if event.attempts >= 3 else 'pending'
                if event.status == 'failed':
                    await add_output(db, binding, key, 'No pude procesar tu mensaje. Puedes escribirlo como texto o solicitar ayuda humana.')
                await db.commit()
                return
            db.add(Message(conversation_id=binding.conversation_id, role=MessageRole.USER, content=text, channel='whatsapp'))
            db.add(Message(conversation_id=binding.conversation_id, role=MessageRole.ASSISTANT, content=response, channel='whatsapp'))
            await add_output(db, binding, key, response)
            event.status = 'done'
            await db.commit()


async def add_output(db, binding, source_id: str, text: str):
    snapshot = await db.get(AgentSnapshot, binding.conversation_id)
    pending = snapshot.data.get('pending') if snapshot and snapshot.data.get('last_turn_id') == source_id else None
    await db.execute(insert(ChannelEvent).values(id=event_key('reply', source_id), binding_id=binding.id,
        kind='outbound', payload={'text': text, 'proposal': pending['fingerprint'] if pending else None}, status='pending', attempts=0,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC)).on_conflict_do_nothing(index_elements=['id']))


async def normalize_message(message: dict, client: WhatsAppClient) -> str:
    kind = message['type']
    if kind == 'text':
        text = str(message['text']['body']).strip()
    elif kind == 'button':
        text = str(message['button']['text']).strip()
    elif kind == 'interactive':
        interactive = message['interactive']
        reply = interactive.get('button_reply') or interactive.get('list_reply') or {}
        text = str(reply.get('title', '')).strip()
    elif kind == 'audio':
        audio, mime = await client.audio(message['audio']['id'])
        from ..features.transcription.service import transcribe_audio
        text = await asyncio.to_thread(transcribe_audio, audio, mime, max_seconds=120)
    else:
        raise ValueError('Unsupported message type')
    if not text or len(text) > 8000:
        raise ValueError('Empty or excessive message')
    return text
