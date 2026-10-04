"""Run only against a disposable, migrated PostgreSQL database."""
import asyncio
import base64
import os
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.agent import jev
from app.agent.runtime import SAFE_REPLY, stateful_stream
from app.agent.state import Signal, now
from app.agent.store import conversation_state
from app.agent.tools.contracts import ToolContext
from app.db.models import (AgentOperation, AgentSnapshot, ChannelBinding, ChannelEvent, Conversation,
                           Customer, Message, Organization, User, UserRole)
from app.db.session import dispose_engine, get_session_factory
from app.features.agent.service import TOOL_REGISTRY
from app.whatsapp.service import event_key, process_event, receive

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_INTEGRATION') != '1',
    reason='RUN_POSTGRES_INTEGRATION=1 requires a disposable migrated PostgreSQL database')]
ARGS = {'date': '2027-10-05', 'time': '19:00', 'party_size': 4, 'customer_name': 'Juan'}


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    await dispose_engine()
    for name, value in [('WHATSAPP_ACCESS_TOKEN', 'test'), ('WHATSAPP_APP_SECRET', 'secret'),
                        ('WHATSAPP_VERIFY_TOKEN', 'verify'), ('WHATSAPP_PHONE_NUMBER_ID', '123')]:
        monkeypatch.setenv(name, value)
    phone = '+1' + str(uuid.uuid4().int)[:10]
    async with get_session_factory()() as db:
        org = Organization(name='Agent test', slug=f'agent-{uuid.uuid4()}')
        db.add(org)
        await db.flush()
        user = User(organization_id=org.id, email=f'{uuid.uuid4()}@test.invalid', password_hash='test', role=UserRole.ADMIN)
        customer = Customer(organization_id=org.id, name='Juan', phone=phone)
        db.add_all([user, customer])
        await db.flush()
        conversation = Conversation(organization_id=org.id, customer_id=customer.id, created_by=user.id, channel='voice', status='open')
        db.add(conversation)
        await db.flush()
        binding = ChannelBinding(organization_id=org.id, conversation_id=conversation.id,
            phone_number_id='123', phone=phone, wa_id=phone[1:], opt_in=True, last_inbound_at=now())
        db.add(binding)
        await db.commit()
    async def observe(state, prompt, turn_id, domain_questions=None):
        return {'confirmation': Signal(value='explicit' if prompt == 'confirmo' else 'uncertain',
            confidence=.99, turn_id=turn_id, model='fake')}
    async def integrity(state, draft, turn_id):
        return Signal(value='supported', confidence=.99, turn_id=turn_id, model='fake')
    monkeypatch.setattr(jev, 'observe', observe)
    monkeypatch.setattr(jev, 'integrity', integrity)
    from app.platform import queue
    monkeypatch.setattr(queue, 'enqueue_channel_work', AsyncMock(return_value=False))
    yield org, conversation, binding
    await dispose_engine()


def context(fixture, request='request', channel='voice'):
    org, conversation, _ = fixture
    return ToolContext(request, organization_id=str(org.id), conversation_id=str(conversation.id), channel=channel)


async def booking_generator(prompt, **kwargs):
    result = await TOOL_REGISTRY.execute('create_booking', ARGS, kwargs['tool_context'])
    yield 'token', {'text': 'Reserva confirmada.' if result.ok else 'Necesito confirmación.'}
    yield 'done', {'provider': 'fake', 'model': 'fake'}


async def turn(fixture, prompt, request, generate=booking_generator, channel='voice'):
    result = [event async for event in stateful_stream(prompt, messages=None, llm=None,
        tool_context=context(fixture, request, channel), generate=generate)]
    if channel == 'voice' and result[-1][1].get('proposal_id'):
        from app.agent.store import mark_presented
        await mark_presented(str(fixture[0].id), str(fixture[1].id), result[-1][1]['proposal_id'])
    return result


async def test_persistent_booking_and_old_turn_replay(fixture):
    first = await turn(fixture, 'reserva', 'one')
    assert 'confirmo' in first[-2][1]['text']
    second = await turn(fixture, 'confirmo', 'two')
    assert second[-2][1]['text'] == 'Reserva confirmada.'
    assert (await turn(fixture, 'reserva', 'one'))[-2][1]['text'] == first[-2][1]['text']
    org, conversation, _ = fixture
    async with get_session_factory()() as db:
        operations = (await db.scalars(select(AgentOperation).where(AgentOperation.conversation_id == conversation.id,
            AgentOperation.tool == 'create_booking'))).all()
        assert len(operations) == 1 and operations[0].status == 'succeeded'
        snapshot = await db.get(AgentSnapshot, conversation.id)
        assert snapshot.data['phase'] == 'completed'
    # New process/session sees the same state and successful booking ledger.
    await dispose_engine()
    async with conversation_state(str(org.id), str(conversation.id)) as store:
        assert store.state.phase == 'completed'
        assert len(store.state.recent) == 4


async def test_foreign_tenant_cannot_load_state(fixture):
    with pytest.raises(ValueError, match='not found'):
        async with conversation_state(str(uuid.uuid4()), str(fixture[1].id)):
            pass


async def test_integrity_blocks_every_token_until_validated(fixture, monkeypatch):
    order = []
    async def generate(prompt, **kwargs):
        order.append('draft')
        yield 'token', {'text': 'Unsupported success!'}
        yield 'done', {'provider': 'fake'}
    async def reject(*args):
        order.append('evaluate')
        return Signal(value='unsupported', confidence=.99, turn_id='one', model='fake')
    monkeypatch.setattr(jev, 'integrity', reject)
    events = await turn(fixture, 'hello', 'one', generate)
    assert order == ['draft', 'evaluate', 'draft', 'evaluate']
    assert [data['text'] for kind, data in events if kind == 'token'] == [SAFE_REPLY]


async def test_concurrent_turns_do_not_lose_updates(fixture):
    async def generate(prompt, **kwargs):
        await asyncio.sleep(.05)
        yield 'token', {'text': prompt}
        yield 'done', {}
    events = await asyncio.gather(turn(fixture, 'one', 'one', generate), turn(fixture, 'two', 'two', generate))
    assert all(result[-1][0] == 'done' for result in events)
    async with get_session_factory()() as db:
        snapshot = await db.get(AgentSnapshot, fixture[1].id)
        assert len(snapshot.data['recent']) == 4


def envelope(binding, messages=None, statuses=None):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': {
        'metadata': {'phone_number_id': '123'}, 'messages': messages or [], 'statuses': statuses or []}}]}]}


async def test_whatsapp_dedupe_durable_recovery_and_delivery(fixture, monkeypatch):
    binding = fixture[2]
    message_id = str(uuid.uuid4())
    payload = envelope(binding, [{'id': message_id, 'from': binding.wa_id, 'timestamp': str(int(now().timestamp())),
        'type': 'text', 'text': {'body': 'reserva'}}])
    await asyncio.gather(receive(payload), receive(payload))
    from app.features.agent import service
    async def agent(prompt, **kwargs):
        async for event in stateful_stream(prompt, messages=None, llm=None, tool_context=kwargs['tool_context'], generate=booking_generator):
            yield event
    monkeypatch.setattr(service, 'stream_agent', agent)
    key = event_key('123', message_id)
    client = AsyncMock()
    client.send.return_value = 'outbound-' + message_id
    await process_event(key, client)
    await process_event(key, client)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == fixture[1].id)) == 2
        snapshot = await db.get(AgentSnapshot, fixture[1].id)
        assert snapshot.data['pending']['presented'] is False
    await process_event(event_key('reply', key), client)
    client.send.assert_awaited_once()
    status = {'id': client.send.return_value, 'recipient_id': binding.wa_id, 'status': 'read', 'timestamp': str(int(now().timestamp()))}
    await receive(envelope(binding, statuses=[status]))
    await process_event(event_key('123', status['id'], 'read', status['timestamp']), client)
    async with get_session_factory()() as db:
        snapshot = await db.get(AgentSnapshot, fixture[1].id)
        assert snapshot.data['pending']['presented'] is True
        output = await db.get(ChannelEvent, event_key('reply', key))
        assert output.status == 'read'


async def test_uncertain_outbound_is_not_repeated(fixture):
    from app.whatsapp.service import add_output
    key = str(uuid.uuid4())
    async with get_session_factory()() as db:
        await add_output(db, fixture[2], key, 'hello')
        await db.commit()
    client = AsyncMock()
    client.send.side_effect = TimeoutError('remote result unknown')
    await process_event(event_key('reply', key), client)
    await process_event(event_key('reply', key), client)
    client.send.assert_awaited_once()
    async with get_session_factory()() as db:
        assert (await db.get(ChannelEvent, event_key('reply', key))).status == 'uncertain'


async def test_callback_reuses_conversation_and_is_not_duplicated(fixture, monkeypatch):
    from app.telephony.telnyx_api import TelnyxApi
    from app.telephony.continuity import restore_outbound
    monkeypatch.setenv('TELNYX_ENABLED', 'true')
    dial = AsyncMock(return_value={'call_control_id': str(uuid.uuid4()), 'call_leg_id': 'leg', 'call_session_id': 'session'})
    monkeypatch.setattr(TelnyxApi, 'dial', dial)
    async def generate(prompt, **kwargs):
        result = await TOOL_REGISTRY.execute('call_customer', {'phone': fixture[2].phone}, kwargs['tool_context'])
        yield 'token', {'text': 'Llamada solicitada.' if result.ok else 'Confirma el teléfono.'}
        yield 'done', {}
    await turn(fixture, 'llámame', 'one', generate)
    assert dial.await_count == 0
    await turn(fixture, 'confirmo', 'two', generate)
    await turn(fixture, 'confirmo', 'two', generate)
    assert dial.await_count == 1
    operation_id = dial.call_args.kwargs['command_id']
    session = await restore_outbound({**dial.return_value, 'client_state': base64.b64encode(operation_id.encode()).decode()})
    assert session.conversation_id == fixture[1].id
    assert session.organization_id == fixture[0].id


async def test_cancelled_local_write_requires_new_confirmation_and_can_recover(fixture):
    from pydantic import BaseModel
    from app.agent.policy import Turn, active_turn, apply_observations, authorize
    from app.agent.tools.contracts import ToolDefinition
    from app.agent.tools.registry import ToolRegistry
    from app.agent.state import fingerprint
    started = asyncio.Event()
    block = True
    class Args(BaseModel):
        value: int
    async def handler(args, context):
        started.set()
        if block:
            await asyncio.Event().wait()
        return {'value': args.value}
    registry = ToolRegistry()
    registry.register(ToolDefinition('local_effect', 'pure local ledger effect', Args, handler, 'write', replay_safe=True))
    org, conversation, _ = fixture
    async with conversation_state(str(org.id), str(conversation.id)) as store:
        authorize(store.state, 'local_effect', {'value': 1})
        store.state.pending.presented = True
        store.state.signals = {'confirmation': Signal(value='explicit', confidence=.99, turn_id='t', model='test')}
        apply_observations(store.state, 'confirmo')
        token = active_turn.set(Turn(store, 't'))
        try:
            task = asyncio.create_task(registry.execute('local_effect', {'value': 1}, context(fixture)))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.state.authorized is None
            operation_id = fingerprint(str(conversation.id), {'proposal': store.state.pending.fingerprint})
            operation = await store.db.get(AgentOperation, operation_id)
            assert operation.status == 'uncertain'
            assert not (await registry.execute('local_effect', {'value': 1}, context(fixture))).ok
            apply_observations(store.state, 'confirmo')
            block = False
            assert (await registry.execute('local_effect', {'value': 1}, context(fixture))).ok
            assert operation.status == 'succeeded'
        finally:
            active_turn.reset(token)


async def test_state_view_exposes_delivery_status_and_block_reason(fixture):
    from app.agent.router import build_state_view
    async with get_session_factory()() as db:
        db.add(ChannelEvent(id=event_key('view', uuid.uuid4().hex), binding_id=fixture[2].id, kind='outbound',
            payload={'text': 'x'}, status='read', attempts=1, external_id='wamid-1',
            created_at=now(), updated_at=now()))
        snapshot = AgentSnapshot(conversation_id=fixture[1].id, organization_id=fixture[0].id,
            data={'key_actions': [{'type': 'response_blocked', 'reason': 'integrity'}]})
        db.add(snapshot)
        await db.commit()
    async with get_session_factory()() as db:
        view = await build_state_view(db, fixture[1].id, fixture[0].id)
        assert view['delivery']['recent_outbound'][0]['status'] == 'read'
        assert view['delivery']['recent_outbound'][0]['external_id'] == 'wamid-1'
        assert view['blocked_reason'] == 'integrity'
        foreign = await build_state_view(db, fixture[1].id, uuid.uuid4())
        assert 'delivery' not in foreign and foreign['conversation_id'] == str(fixture[1].id)


async def test_callback_webhook_reconciles_lost_dial_response(fixture):
    from app.telephony.continuity import restore_outbound
    operation_id = uuid.uuid4().hex + uuid.uuid4().hex
    async with get_session_factory()() as db:
        db.add(AgentOperation(id=operation_id, organization_id=fixture[0].id, conversation_id=fixture[1].id,
            tool='call_customer', arguments={'phone': fixture[2].phone}, status='uncertain'))
        await db.commit()
    restored = await restore_outbound({'client_state': base64.b64encode(operation_id.encode()).decode(),
        'call_control_id': str(uuid.uuid4()), 'call_leg_id': 'leg', 'call_session_id': 'session'})
    assert restored.conversation_id == fixture[1].id
    async with get_session_factory()() as db:
        operation = await db.get(AgentOperation, operation_id)
        assert operation.status == 'succeeded'
        assert operation.result['call_control_id'] == restored.telnyx_call_control_id
