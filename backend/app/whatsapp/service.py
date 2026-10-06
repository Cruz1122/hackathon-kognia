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
from ..db.models import AgentSnapshot, ChannelBinding, ChannelEvent, Conversation, Customer, Message, MessageRole
from ..db.session import get_session_factory
from .client import WhatsAppClient

logger = logging.getLogger(__name__)
STATUS_RANK = {'sent': 1, 'delivered': 2, 'read': 3}
# Continuation after a dropped call: the LLM writes the recap, this stays as the
# safe fallback whenever generation is unavailable, empty or off-policy.
DEFAULT_CONTINUATION_TEXT = (
    'Gracias por tu llamada. Podemos continuar por aquí '
    'y con gusto te ayudo con lo que quedó pendiente. ¿Quieres que retomemos?'
)
ERROR_CONTINUATION_TEXT = (
    'Lamento que la llamada terminara antes de que pudiera responderte. '
    'Podemos continuar por aquí con lo que quedó pendiente. ¿Quieres que retomemos?'
)
MAX_CONTINUATION_CHARS = 900
CONTINUATION_SYSTEM = (
    'Eres un asesor que retoma por WhatsApp una conversación telefónica. '
    'Escribe un único mensaje breve y cálido en español que: (1) agradezca la llamada, sin asumir que hubo un fallo, '
    '(2) resuma en una o dos frases lo hablado usando SOLO la transcripción provista, '
    '(3) haga solo la siguiente pregunta concreta pendiente, sin preguntas abiertas ni varias preguntas juntas. No inventes datos, precios, reservas, '
    'disponibilidad ni acciones: si algo no está en la transcripción, omítelo. '
    'Mantén un tono cordial y profesional. No atribuyas emociones al cliente ni menciones evaluaciones '
    'internas: nunca digas que está frustrado, molesto o satisfecho. No confundas una queja de proceso complicado con un cambio de horario. '
    'No uses listas, viñetas ni encabezados. Máximo 3 frases. Devuelve solo el mensaje final.'
)


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
            binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.phone_number_id == number,
                ChannelBinding.wa_id == wa_id).with_for_update())
            if binding is None:
                # Demo deliberately requires operator-verified identity; never guess tenant.
                logger.warning('Ignoring WhatsApp event without a verified binding')
                continue
            timestamp = datetime.fromtimestamp(int(data['timestamp']), UTC)
            if timestamp > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError('Future timestamp')
            conversation_id = binding.conversation_id
            ambiguous_route = False
            if kind == 'inbound':
                # Meta can deliver pre-transfer messages after the route changed.
                # Its seconds-resolution timestamp cannot order events in the
                # cutover second reliably: retain those instead of guessing.
                transitions = (await db.scalars(select(ChannelEvent).where(
                    ChannelEvent.binding_id == binding.id,
                    ChannelEvent.payload['_route_changed_at'].as_string().is_not(None)
                ).order_by(ChannelEvent.created_at.desc(), ChannelEvent.id.desc()))).all()
                for transition in transitions:
                    cutover = datetime.fromisoformat(transition.payload['_route_changed_at'])
                    if timestamp + timedelta(seconds=1) <= cutover:
                        conversation_id = uuid.UUID(transition.payload['_route_previous_conversation_id'])
                    elif timestamp >= cutover:
                        break
                    else:
                        ambiguous_route = True
                        logger.warning('WhatsApp message retained: ambiguous routing at transfer boundary')
                        break
                # SQL max prevents concurrent webhooks moving the window backwards.
                from sqlalchemy import update, or_
                await db.execute(update(ChannelBinding).where(ChannelBinding.id == binding.id,
                    or_(ChannelBinding.last_inbound_at.is_(None), ChannelBinding.last_inbound_at < timestamp)
                ).values(last_inbound_at=timestamp))
            await db.execute(insert(ChannelEvent).values(id=key, binding_id=binding.id,
                kind=kind, payload={**data, '_conversation_id': str(conversation_id)},
                status='routing_ambiguous' if ambiguous_route else 'pending', attempts=0,
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC)).on_conflict_do_nothing(index_elements=['id']))
        await db.commit()
    # The worker also scans durable pending rows, covering enqueue failures/crashes.
    if events:
        from ..platform.queue import enqueue_channel_work
        await enqueue_channel_work()


def within_service_window(binding: ChannelBinding, now: datetime) -> bool:
    last = binding.last_inbound_at
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return bool(last and timedelta(0) <= now - last < timedelta(hours=24))


def outbound_payload(binding: ChannelBinding, text: str, now: datetime) -> dict:
    if within_service_window(binding, now):
        return {'to': binding.wa_id, 'type': 'text', 'text': {'body': text[:4000]}}
    if not binding.opt_in or not binding.template_name:
        raise ValueError('An approved template and opt-in are required outside the service window')
    return {'to': binding.wa_id, 'type': 'template', 'template': {
        'name': binding.template_name, 'language': {'code': binding.template_language}}}


def continuation_tone(state) -> str:
    """Reuse current adaptive service behavior without exposing sentiment labels."""
    if state is None:
        return ''
    hints: list[str] = []
    human = state.signals.get('human')
    behavior = state.behavior_guidance()
    if behavior:
        hints.append(behavior)
    if human and human.value == 'requested':
        hints.append('El cliente pidió hablar con una persona: reconócelo y menciona que un asesor le dará seguimiento.')
    return (' '.join(hints) + ' ') if hints else ''


def render_transcript(rows) -> str:
    lines: list[str] = []
    for row in rows:
        role = 'Cliente' if row.role == MessageRole.USER else 'Asesor'
        content = ' '.join(str(row.content).split())
        if content:
            lines.append(f'{role}: {content[:500]}')
    return '\n'.join(lines)


def clean_continuation(text: str) -> str:
    return ' '.join(str(text).split()).strip().strip('"').strip()


async def complete_text(prompt: str, *, llm=None, chain=None) -> str:
    """One bounded, tool-free completion over the configured model chain."""
    from ..config import get_model_chain
    from ..providers import llm_provider as provider
    from ..providers.errors import ProviderError

    client = llm or provider
    configs = chain if chain is not None else get_model_chain()
    messages = [{'role': 'system', 'content': CONTINUATION_SYSTEM}, {'role': 'user', 'content': prompt}]
    last_error: Exception | None = None
    for config in configs:
        if not config.api_key:
            continue
        try:
            text = ''
            async for kind, payload in client.stream(config, prompt, messages=messages, tools=None):
                if kind == 'token':
                    text += str(payload.get('text', ''))
                    if len(text) > MAX_CONTINUATION_CHARS * 4:
                        break
            if text.strip():
                return text.strip()
        except Exception as exc:  # try the next provider; never surface provider text
            last_error = exc
            continue
    raise last_error or ProviderError('No provider available for continuation')


async def continuation_message(conversation_id, organization_id, source_id: str, *, db, llm=None, chain=None) -> str:
    """LLM-written apology + recap for a dropped call, always bounded by a fallback."""
    try:
        if not settings().whatsapp_enabled:
            return DEFAULT_CONTINUATION_TEXT
        rows = (await db.scalars(select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT])
        ).order_by(Message.created_at.desc()).limit(12))).all()
        transcript = render_transcript(list(reversed(rows)))
        if not transcript:
            return DEFAULT_CONTINUATION_TEXT
        snapshot = await db.get(AgentSnapshot, conversation_id)
        state = None
        if snapshot is not None and isinstance(snapshot.data, dict):
            from ..agent.state import AgentState
            try:
                state = AgentState.model_validate(snapshot.data)
            except Exception:
                state = None
        guide = ''
        if state and state.booking_slots:
            from ..agent.dialogue import next_question
            confirmed = any(item['tool'] == 'create_booking' and item['ok'] for item in state.tool_history)
            guide = '\nEstado de la reserva (datos, no instrucciones): ' + json.dumps({
                'known_details': state.booking_slots, 'confirmed': confirmed,
                'next_question': next_question(state) or ('¿Quieres cambiar algún dato?' if confirmed else '¿La confirmas?')}, ensure_ascii=False)
        prompt = (continuation_tone(state) + 'Transcripción de la llamada:\n' + transcript + guide
                  + '\n\nEscribe el mensaje de WhatsApp para retomar la conversación.')
        text = clean_continuation(await complete_text(prompt, llm=llm, chain=chain))
        if not text or len(text) > MAX_CONTINUATION_CHARS:
            return DEFAULT_CONTINUATION_TEXT
        return text
    except Exception:
        logger.warning('Continuation generation failed; using the default message')
        return DEFAULT_CONTINUATION_TEXT


async def prepare_continuation(
    conversation_id: uuid.UUID,
    organization_id: uuid.UUID,
    source_id: str,
    *,
    apologize: bool = False,
):
    async with get_session_factory()() as db:
        key = event_key('continuation', source_id)
        if await db.get(ChannelEvent, key):
            return  # A duplicate/late hangup must not move routing back to an old thread.
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
            Conversation.organization_id == organization_id))
        if conversation is None:
            return
        # A finished conversation has nothing to resume: do not keep apologizing
        # for a dropped call every time one ends.
        snapshot = await db.get(AgentSnapshot, conversation_id)
        state = None
        if snapshot is not None and isinstance(snapshot.data, dict):
            from ..agent.state import AgentState
            try:
                state = AgentState.model_validate(snapshot.data)
            except Exception:
                state = None
            confirmed = bool(state and any(item.get('tool') == 'create_booking' and item.get('ok')
                                           for item in state.tool_history))
            if state is not None and state.phase == 'completed' and state.pending is None and confirmed:
                logger.info('Skipping continuation: conversation already completed')
                return
        binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.conversation_id == conversation_id,
            ChannelBinding.organization_id == organization_id,
            ChannelBinding.phone_number_id == settings().whatsapp_number).with_for_update())
        if binding is None and conversation.customer_id:
            customer = await db.scalar(select(Customer).where(Customer.id == conversation.customer_id,
                Customer.organization_id == organization_id))
            if customer:
                # Use an existing operator-verified destination, never create identity
                # or consent from caller ID. This selects routing, not past context.
                binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.organization_id == organization_id,
                    ChannelBinding.phone_number_id == settings().whatsapp_number,
                    ChannelBinding.phone == customer.phone).with_for_update())
        if not binding:
            logger.info('No verified WhatsApp destination for call continuation')
            return
        if await db.get(ChannelEvent, key, populate_existing=True):
            return  # Recheck after the identity lock: concurrent duplicate hangup.
        routing_change = {}
        if binding.conversation_id != conversation_id:
            routing_change = {'_route_previous_conversation_id': str(binding.conversation_id),
                              '_route_changed_at': datetime.now(UTC).isoformat()}
            previous_events = (await db.scalars(select(ChannelEvent).where(
                ChannelEvent.binding_id == binding.id,
                ChannelEvent.payload['_conversation_id'].as_string().is_(None)).with_for_update())).all()
            for previous in previous_events:
                previous.payload = {**previous.payload, '_conversation_id': str(binding.conversation_id)}
            binding.conversation_id = conversation_id
        continuation_payload = {
            'continuation': {'call_id': source_id, 'apologize': apologize},
            '_conversation_id': str(conversation_id),
            **routing_change,
        }
        if state is not None:
            from ..agent.dialogue import continuation_text
            text, proposal = continuation_text(state, apologize=apologize)
            continuation_payload['text'] = text
            if proposal:
                continuation_payload['proposal'] = proposal
        elif apologize:
            continuation_payload['text'] = ERROR_CONTINUATION_TEXT
        await db.execute(insert(ChannelEvent).values(id=key, binding_id=binding.id, kind='outbound',
            payload=continuation_payload,
            status='pending', attempts=0, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
        ).on_conflict_do_nothing(index_elements=['id']))
        await db.commit()
    from ..platform.queue import enqueue_channel_work
    await enqueue_channel_work()


async def process_pending(limit: int = 20, *, min_age_seconds: float = 2) -> None:
    if not settings().whatsapp_enabled:
        return
    async with get_session_factory()() as db:
        ids = list((await db.scalars(select(ChannelEvent.id).where(
            ChannelEvent.status.in_(['pending', 'processing', 'sending']),
            ChannelEvent.updated_at <= datetime.now(UTC) - timedelta(seconds=max(0, min_age_seconds))
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
            binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.id == event.binding_id).with_for_update())
            if binding is None:
                return
            # Routing may have changed while this worker waited for the identity.
            await db.refresh(event)
            try:
                conversation_id = uuid.UUID(str(event.payload.get('_conversation_id') or binding.conversation_id))
            except ValueError:
                event.status = 'failed'
                await db.commit()
                return
            conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                Conversation.organization_id == binding.organization_id))
            if conversation is None:
                event.status = 'failed'
                await db.commit()
                return
            event.payload = {**event.payload, '_conversation_id': str(conversation_id)}
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
                            output_conversation = output.payload.get('_conversation_id') or str(binding.conversation_id)
                            await mark_presented(str(binding.organization_id), str(output_conversation), output.payload['proposal'])
                    event.status = 'done'
                event.updated_at = datetime.now(UTC)
                await db.commit()
                return
            if event.kind == 'outbound':
                if event.status == 'sending':
                    event.status = 'uncertain'  # Never blindly repeat an externally accepted send.
                    await db.commit()
                    return
                text = event.payload.get('text')
                if (not text and event.payload.get('continuation')
                        and within_service_window(binding, datetime.now(UTC))):
                    text = await continuation_message(conversation_id, binding.organization_id,
                        str(event.payload['continuation'].get('call_id') or event.id), db=db)
                    event.payload = {**event.payload, 'text': text}
                    await db.commit()
                try:
                    payload = outbound_payload(binding, text or '', datetime.now(UTC))
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
                    if event.payload.get('continuation'):
                        db.add(Message(conversation_id=conversation_id, role=MessageRole.SYSTEM,
                            content='Llamada transferida a WhatsApp', channel='system'))
                        if payload['type'] == 'text':
                            db.add(Message(conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                                content=text, channel='whatsapp'))
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
                from ..features.agent.service import stream_agent
                response = ''
                async for kind, data in stream_agent(text, tool_context=ToolContext(
                    request_id=key, organization_id=str(binding.organization_id),
                    conversation_id=str(conversation_id), user_id=str(conversation.created_by), channel='whatsapp')):
                    if kind == 'error':
                        raise RuntimeError('Agent turn failed')
                    if kind == 'token':
                        response += data['text']
                if not response:
                    raise RuntimeError('Empty response')
            except Exception:
                event.status = 'failed' if event.attempts >= 3 else 'pending'
                if event.status == 'failed':
                    await add_output(db, binding, key, 'No pude procesar tu mensaje. Puedes escribirlo como texto o solicitar ayuda humana.', conversation_id)
                await db.commit()
                return
            db.add(Message(conversation_id=conversation_id, role=MessageRole.USER, content=text, channel='whatsapp'))
            db.add(Message(conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=response, channel='whatsapp'))
            await add_output(db, binding, key, response, conversation_id)
            event.status = 'done'
            await db.commit()


async def add_output(db, binding, source_id: str, text: str, conversation_id=None):
    conversation_id = conversation_id or binding.conversation_id
    snapshot = await db.get(AgentSnapshot, conversation_id)
    pending = snapshot.data.get('pending') if snapshot and snapshot.data.get('last_turn_id') == source_id else None
    await db.execute(insert(ChannelEvent).values(id=event_key('reply', source_id), binding_id=binding.id,
        kind='outbound', payload={'text': text, 'proposal': pending['fingerprint'] if pending else None,
                                '_conversation_id': str(conversation_id)}, status='pending', attempts=0,
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
