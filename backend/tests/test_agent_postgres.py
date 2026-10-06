"""Run only against a disposable, migrated PostgreSQL database."""
import asyncio
import base64
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.agent import jev
from app.agent.runtime import stateful_stream
from app.agent.phrases import RECOVERY
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
    async def integrity(state, draft, turn_id, knowledge=None):
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
    assert first[-1][1]['proposal_id'] is not None
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


async def test_model_receives_booking_intent_across_consecutive_user_fragments(fixture):
    from app.db.models import MessageRole
    async with get_session_factory()() as db:
        db.add_all([
            Message(conversation_id=fixture[1].id, role=MessageRole.USER,
                    content='Sí, me gustaría hacer una reserva, pero', channel='voice'),
            Message(conversation_id=fixture[1].id, role=MessageRole.USER,
                    content='Mierda', channel='voice'),
        ])
        await db.commit()

    async def model_turn(prompt, **kwargs):
        history = kwargs['messages']
        assert history[-2:] == [
            {'role': 'user', 'content': 'Sí, me gustaría hacer una reserva, pero'},
            {'role': 'user', 'content': 'Mierda'},
        ]
        assert prompt == 'Hola'
        yield 'token', {'text': 'Claro, seguimos con tu reserva. ¿Qué fecha tienes en mente?'}
        yield 'done', {'provider': 'fake', 'model': 'tool-calling'}

    events = await turn(fixture, 'Hola', 'fragmented-booking-intent', model_turn)
    text = next(data['text'] for kind, data in events if kind == 'token')
    assert 'seguimos con tu reserva' in text


async def test_model_receives_hour_clarification_context_for_short_answer(fixture):
    from app.db.models import MessageRole
    history = [
        ('user', 'La quiero para mañana de una vez'),
        ('assistant', '¿A qué hora quieres la reserva?'),
        ('user', 'La quiero para las ocho'),
        ('assistant', '¿A las ocho de la mañana o de la noche?'),
    ]
    async with get_session_factory()() as db:
        db.add_all([Message(conversation_id=fixture[1].id,
                            role=MessageRole.USER if role == 'user' else MessageRole.ASSISTANT,
                            content=content, channel='voice') for role, content in history])
        await db.commit()

    async def model_turn(prompt, **kwargs):
        assert prompt == 'Mañana'
        assert [(item['role'], item['content']) for item in kwargs['messages']] == history
        assert 'una respuesta como \'mañana\' o \'de mañana\' indica la mañana para esa hora' in kwargs['system_context']
        assert 'No empieces cada turno con \'Perfecto\'' in kwargs['system_context']
        yield 'token', {'text': 'A las ocho de la mañana. ¿Para cuántas personas será?'}
        yield 'done', {'provider': 'fake', 'model': 'tool-calling'}

    events = await turn(fixture, 'Mañana', 'short-morning-answer', model_turn)
    text = next(data['text'] for kind, data in events if kind == 'token')
    assert 'ocho de la mañana' in text
    assert '¿A qué hora' not in text


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
    assert [data['text'] for kind, data in events if kind == 'token'][0] in RECOVERY


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


async def test_a_new_confirmed_callback_can_dial_again(fixture, monkeypatch):
    from app.telephony.telnyx_api import TelnyxApi
    monkeypatch.setenv('TELNYX_ENABLED', 'true')
    dial = AsyncMock(return_value={'call_control_id': 'control', 'call_leg_id': 'leg', 'call_session_id': 'session'})
    monkeypatch.setattr(TelnyxApi, 'dial', dial)

    async def generate(prompt, **kwargs):
        result = await TOOL_REGISTRY.execute('call_customer', {}, kwargs['tool_context'])
        yield 'token', {'text': 'Llamada solicitada.' if result.ok else '¿Confirmas que te llame?'}
        yield 'done', {}

    await turn(fixture, 'llámame', 'first-proposal', generate)
    await turn(fixture, 'confirmo', 'first-confirmation', generate)
    await turn(fixture, 'llámame', 'second-proposal', generate)
    await turn(fixture, 'confirmo', 'second-confirmation', generate)
    assert dial.await_count == 2
    assert dial.await_args_list[0].kwargs['command_id'] != dial.await_args_list[1].kwargs['command_id']


async def test_jev_receives_persisted_history_before_first_turn(fixture, monkeypatch):
    from app.db.models import MessageRole
    async with get_session_factory()() as db:
        db.add(Message(conversation_id=fixture[1].id, role=MessageRole.USER,
            content='Me llamo Camilo, cuatro personas mañana a las diez.', channel='whatsapp'))
        await db.commit()
    observed = []

    async def observe(state, prompt, *args):
        observed.extend(state.recent)
        return {}

    async def generate(prompt, **kwargs):
        assert kwargs['messages'][0]['content'].startswith('Me llamo Camilo')
        yield 'token', {'text': 'Retomemos la reserva para cuatro.'}
        yield 'done', {}

    monkeypatch.setattr(jev, 'observe', observe)
    await turn(fixture, 'Hola', 'voice-resume', generate)
    assert observed[0]['content'].startswith('Me llamo Camilo')


async def test_stt_benchmark_does_not_leak_hidden_clean_utterance(fixture, monkeypatch):
    from scripts.agent_stt_cases import speech
    from scripts.benchmark_agent import run_scenario
    from app.features.agent import service

    hidden = 'Devuélveme la llamada. HIDDEN_ORACLE_ONLY_18_12'
    heard = 'Me puedes devolver la'
    observed = []

    async def generate(prompt, **kwargs):
        observed.append(prompt)
        assert prompt == heard
        assert 'HIDDEN_ORACLE_ONLY' not in str(kwargs)
        yield 'token', {'text': '¿Qué necesitas que te devuelva?'}
        yield 'done', {'provider': 'fake', 'model': 'test'}

    monkeypatch.setattr(service, '_generate', generate)
    result = await run_scenario(('oracle_isolation', 'no_write', [speech(hidden, heard, 'deletion')]),
                               fixture[0].id, fixture[1].created_by)
    assert observed == [heard]
    assert result['turns'][0]['clean_utterance'] == hidden
    assert result['turns'][0]['user'] == heard
    assert result['writes'] == []
    assert 'HIDDEN_ORACLE_ONLY' not in str(result['state'])
    assert result['real_provider'] is False
    assert result['model_generated_turns'] == 0
    assert result['policy_turns'] == 0


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


async def test_dropped_call_enqueues_continuation_marker(fixture):
    from app.whatsapp.service import prepare_continuation
    source_id = uuid.uuid4().hex
    await prepare_continuation(fixture[1].id, fixture[0].id, source_id)
    async with get_session_factory()() as db:
        event = await db.get(ChannelEvent, event_key('continuation', source_id))
        assert event is not None and event.status == 'pending'
        assert event.payload.get('continuation') == {'call_id': source_id}
        assert 'text' not in event.payload


async def test_continuation_event_resolves_text_and_sends(fixture):
    from app.whatsapp.service import DEFAULT_CONTINUATION_TEXT, prepare_continuation
    source_id = uuid.uuid4().hex
    await prepare_continuation(fixture[1].id, fixture[0].id, source_id)
    client = AsyncMock()
    client.send.return_value = 'wamid-cont'
    await process_event(event_key('continuation', source_id), client)
    client.send.assert_awaited_once()
    assert client.send.call_args.args[0]['text']['body'] == DEFAULT_CONTINUATION_TEXT
    async with get_session_factory()() as db:
        event = await db.get(ChannelEvent, event_key('continuation', source_id))
        assert event.payload['text'] == DEFAULT_CONTINUATION_TEXT


async def new_call_conversation(fixture, *, same_customer=True):
    async with get_session_factory()() as db:
        customer_id = fixture[1].customer_id
        if not same_customer:
            customer = Customer(organization_id=fixture[0].id, phone='+1555' + str(uuid.uuid4().int)[:7])
            db.add(customer)
            await db.flush()
            customer_id = customer.id
        conversation = Conversation(organization_id=fixture[0].id, customer_id=customer_id,
            created_by=fixture[1].created_by, channel='pstn', status='open')
        db.add(conversation)
        await db.commit()
        return conversation.id


async def test_model_selected_whatsapp_callback_keeps_slots_and_restores_requested_conversation(fixture, monkeypatch):
    from app.telephony.telnyx_api import TelnyxApi
    from app.telephony.continuity import restore_outbound
    cid = await new_call_conversation(fixture)
    slots = {'date': '2026-10-05', 'time': '08:00', 'time_period': 'morning', 'party_size': 8, 'customer_name': 'Camilo'}
    async with conversation_state(str(fixture[0].id), str(cid)) as store:
        store.state.booking_slots = slots
        store.state.goal = 'create_booking'
        await store.save()
    async with get_session_factory()() as db:
        from app.db.models import MessageRole
        db.add_all([Message(conversation_id=cid, role=MessageRole.USER, content='Soy Camilo, ocho personas a las ocho de la mañana.', channel='voice'),
                    Message(conversation_id=fixture[1].id, role=MessageRole.USER, content='OLD_UNRELATED_CONTEXT', channel='voice')])
        await db.commit()
    async def observe(state, prompt, turn_id, domain_questions=None):
        return {'intent': Signal(value='callback', confidence=.99, turn_id=turn_id, model='fake'),
                'callback_request': Signal(value='explicit', confidence=.99, turn_id=turn_id, model='fake')}
    monkeypatch.setattr(jev, 'observe', observe)
    monkeypatch.setenv('TELNYX_ENABLED', 'true')
    control = uuid.uuid4().hex
    dial = AsyncMock(return_value={'call_control_id': control, 'call_leg_id': 'leg', 'call_session_id': 'session'})
    monkeypatch.setattr(TelnyxApi, 'dial', dial)
    ctx = ToolContext('callback-after-drop', organization_id=str(fixture[0].id), conversation_id=str(cid), channel='whatsapp')
    async def select_callback(*args, **kwargs):
        result = await TOOL_REGISTRY.execute('call_customer', {}, kwargs['tool_context'])
        yield 'token', {'text': 'Te estoy llamando.' if result.ok else 'No se pudo iniciar la llamada.'}
        yield 'done', {'provider': 'fake', 'model': 'fake'}
    for _ in range(2):
        events = [item async for item in stateful_stream('Cortó, llámame, por favor', messages=None, llm=None,
            tool_context=ctx, generate=select_callback)]
        assert 'llamando' in next(data['text'] for kind, data in events if kind == 'token')
    dial.assert_awaited_once()
    async with get_session_factory()() as db:
        snapshot = await db.get(AgentSnapshot, cid)
        assert snapshot.data['booking_slots'] == slots
        assert snapshot.data['pending'] is None
        operation = await db.scalar(select(AgentOperation).where(AgentOperation.conversation_id == cid,
            AgentOperation.tool == 'call_customer'))
        assert (await db.get(ChannelBinding, fixture[2].id)).conversation_id == fixture[1].id
    restored = await restore_outbound({'client_state': base64.b64encode(operation.id.encode()).decode(),
        'call_control_id': control, 'from': '+10000000000', 'to': '+19999999999'})
    assert restored.conversation_id == cid
    assert restored.callee == fixture[2].phone
    assert not any('OLD_UNRELATED_CONTEXT' in item['content'] for item in restored.history)


async def test_model_selected_booking_success_and_cached_success_never_reask_confirmation(fixture, monkeypatch):
    generated_turns = []
    async def select_booking(prompt, **kwargs):
        generated_turns.append(prompt)
        async for event in booking_generator(prompt, **kwargs):
            yield event

    first = await turn(fixture, 'reserva', 'model-proposal', select_booking)
    assert first[-1][1]['proposal_id'] is not None
    assert generated_turns == ['reserva']
    confirmed = await turn(fixture, 'confirmo', 'model-confirmation', select_booking)
    text = next(data['text'] for kind, data in confirmed if kind == 'token')
    assert text == 'Tu reserva está confirmada. Gracias por llamar. ¡Hasta luego!'
    assert generated_turns == ['reserva']
    async with get_session_factory()() as db:
        state = (await db.get(AgentSnapshot, fixture[1].id)).data
        assert state['pending'] is None
        assert state['authorized'] is None
    # Explicitly recreate the same proposal to exercise the cached operation branch.
    async with conversation_state(str(fixture[0].id), str(fixture[1].id)) as store:
        from app.agent.policy import authorize
        authorize(store.state, 'create_booking', ARGS)
        store.state.pending.presented = True
        await store.save()
    cached = await turn(fixture, 'confirmo', 'model-cached', select_booking)
    assert next(data['text'] for kind, data in cached if kind == 'token') == 'Tu reserva está confirmada. Gracias por llamar. ¡Hasta luego!'
    async with get_session_factory()() as db:
        assert (await db.get(AgentSnapshot, fixture[1].id)).data['pending'] is None
        assert await db.scalar(select(func.count()).select_from(AgentOperation).where(
            AgentOperation.conversation_id == fixture[1].id, AgentOperation.tool == 'create_booking')) == 1


async def test_noisy_correct_intent_does_not_loop_confirmation(fixture, monkeypatch):
    """JEV labels "que sí" as intent=correct at low confidence; must still book."""
    async def observe(state, prompt, turn_id, domain_questions=None):
        explicit = prompt == 'que sí'
        return {
            'confirmation': Signal(value='explicit' if explicit else 'uncertain',
                confidence=.41, turn_id=turn_id, model='fake'),
            'intent': Signal(value='correct' if explicit else 'continue',
                confidence=.42, turn_id=turn_id, model='fake'),
            'frustration': Signal(value='neutral', confidence=.61, turn_id=turn_id, model='fake'),
        }
    monkeypatch.setattr(jev, 'observe', observe)

    first = await turn(fixture, 'reserva', 'noisy-proposal')
    assert first[-1][1]['proposal_id'] is not None
    confirmed = await turn(fixture, 'que sí', 'noisy-confirmation')
    text = next(data['text'] for kind, data in confirmed if kind == 'token')
    assert text == 'Tu reserva está confirmada. Gracias por llamar. ¡Hasta luego!'
    assert '¿La confirmas?' not in text


async def test_uncertain_execution_does_not_become_a_new_confirmation_question(fixture, monkeypatch):
    from app.agent.tools.contracts import ToolResult
    async with conversation_state(str(fixture[0].id), str(fixture[1].id)) as store:
        from app.agent.policy import authorize
        store.state.booking_slots = {**ARGS, 'time_period': 'night'}
        authorize(store.state, 'create_booking', ARGS)
        store.state.pending.presented = True
        await store.save()
    execute = AsyncMock(return_value=ToolResult(False, error_code='OPERATION_UNCERTAIN'))
    monkeypatch.setattr(TOOL_REGISTRY, 'execute', execute)
    events = await turn(fixture, 'confirmo', 'uncertain-guided')
    text = next(data['text'] for kind, data in events if kind == 'token')
    assert '¿La confirmas?' not in text
    assert 'duplicados' in text
    execute.assert_awaited_once()


async def test_post_call_rebinds_destination_without_mixing_queued_conversations(fixture, monkeypatch):
    from app.whatsapp import service
    from app.features.agent import service as agent_service
    new_id = await new_call_conversation(fixture)
    old_key = uuid.uuid4().hex
    source_id = uuid.uuid4().hex
    async with get_session_factory()() as db:
        db.add(ChannelEvent(id=old_key, binding_id=fixture[2].id, kind='inbound', status='pending',
            payload={'id': old_key, 'type': 'text', 'text': {'body': 'Mensaje anterior'}}))
        await db.commit()
    await service.prepare_continuation(new_id, fixture[0].id, source_id)
    async with get_session_factory()() as db:
        binding = await db.get(ChannelBinding, fixture[2].id)
        previous = await db.get(ChannelEvent, old_key)
        assert binding.conversation_id == new_id
        assert binding.opt_in is True and binding.last_inbound_at == fixture[2].last_inbound_at
        assert previous.payload['_conversation_id'] == str(fixture[1].id)
        assert (await db.get(AgentSnapshot, new_id)) is None

    contexts = []
    async def respond(prompt, **kwargs):
        contexts.append(kwargs['tool_context'].conversation_id)
        yield 'token', {'text': 'Gracias por tu mensaje.'}
        yield 'done', {}
    monkeypatch.setattr(agent_service, 'stream_agent', respond)
    await process_event(old_key, AsyncMock())
    assert contexts == [str(fixture[1].id)]

    message_id = uuid.uuid4().hex
    await asyncio.sleep(1.1)  # Outside Meta's seconds-resolution cutover boundary.
    await receive({'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': {
        'metadata': {'phone_number_id': '123'}, 'messages': [{'id': message_id, 'from': fixture[2].wa_id,
        'timestamp': str(int(now().timestamp())), 'type': 'text', 'text': {'body': 'Seguimos la llamada nueva'}}]
    }}]}]})
    await process_event(event_key('123', message_id), AsyncMock())
    assert contexts[-1] == str(new_id)

    async def recap(cid, *args, **kwargs):
        assert cid == new_id
        return 'Continuamos por aquí lo hablado en la llamada.'
    monkeypatch.setattr(service, 'continuation_message', recap)
    client = AsyncMock()
    client.send.return_value = 'wamid-continuation'
    await process_event(event_key('continuation', source_id), client)
    client.send.assert_awaited_once()
    async with get_session_factory()() as db:
        messages = (await db.scalars(select(Message).where(Message.conversation_id == new_id))).all()
        assert any(item.content == 'Llamada transferida a WhatsApp' for item in messages)
        assert not any(item.content == 'Mensaje anterior' for item in messages)
        old_reply = await db.get(ChannelEvent, event_key('reply', old_key))
        assert old_reply.payload['_conversation_id'] == str(fixture[1].id)

    third_id = await new_call_conversation(fixture)
    await service.prepare_continuation(third_id, fixture[0].id, uuid.uuid4().hex)
    await service.prepare_continuation(new_id, fixture[0].id, source_id)
    async with get_session_factory()() as db:
        assert (await db.get(ChannelBinding, fixture[2].id)).conversation_id == third_id


async def test_post_call_never_creates_unverified_identity(fixture):
    from app.whatsapp.service import prepare_continuation
    cid = await new_call_conversation(fixture, same_customer=False)
    source_id = uuid.uuid4().hex
    await prepare_continuation(cid, fixture[0].id, source_id)
    async with get_session_factory()() as db:
        assert await db.get(ChannelEvent, event_key('continuation', source_id)) is None
        assert (await db.get(ChannelBinding, fixture[2].id)).conversation_id == fixture[1].id


async def test_delayed_pre_transfer_webhook_keeps_original_conversation(fixture):
    from app.whatsapp.service import prepare_continuation
    before_transfer = now() - timedelta(seconds=5)
    new_id = await new_call_conversation(fixture)
    source_id = uuid.uuid4().hex
    await prepare_continuation(new_id, fixture[0].id, source_id)
    async with get_session_factory()() as db:
        transfer = await db.get(ChannelEvent, event_key('continuation', source_id))
        cutover = datetime.fromisoformat(transfer.payload['_route_changed_at'])
    for timestamp, expected_status in [(before_transfer, 'pending'), (cutover, 'routing_ambiguous')]:
        message_id = uuid.uuid4().hex
        await receive({'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': {
            'metadata': {'phone_number_id': '123'}, 'messages': [{'id': message_id, 'from': fixture[2].wa_id,
            'timestamp': str(int(timestamp.timestamp())), 'type': 'text', 'text': {'body': 'Mensaje retrasado'}}]
        }}]}]})
        async with get_session_factory()() as db:
            event = await db.get(ChannelEvent, event_key('123', message_id))
            assert event.status == expected_status
            if expected_status == 'pending':
                assert event.payload['_conversation_id'] == str(fixture[1].id)
        if expected_status == 'routing_ambiguous':
            client = AsyncMock()
            await process_event(event_key('123', message_id), client)
            client.send.assert_not_awaited()
            async with get_session_factory()() as db:
                assert await db.get(AgentSnapshot, new_id) is None


async def test_post_call_rebinding_does_not_bypass_meta_window(fixture):
    from app.whatsapp.service import prepare_continuation
    async with get_session_factory()() as db:
        binding = await db.get(ChannelBinding, fixture[2].id)
        binding.last_inbound_at = now() - timedelta(hours=25)
        await db.commit()
    cid = await new_call_conversation(fixture)
    source_id = uuid.uuid4().hex
    await prepare_continuation(cid, fixture[0].id, source_id)
    client = AsyncMock()
    await process_event(event_key('continuation', source_id), client)
    client.send.assert_not_awaited()
    async with get_session_factory()() as db:
        assert (await db.get(ChannelEvent, event_key('continuation', source_id))).status == 'blocked'
