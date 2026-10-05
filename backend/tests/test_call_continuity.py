import asyncio
import time
import uuid
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.phrases import HOLDING, RECOVERY, SILENCE, pick
from app.telephony import runtime as runtime_module
from app.telephony.bridge import _with_holding
from app.telephony.runtime import TelephonyRuntime
from app.telephony.sessions import CallSession


def session():
    return CallSession(call_id=uuid.uuid4(), token='test', telnyx_call_control_id='control')


@pytest.mark.asyncio
@pytest.mark.parametrize('stale_attachment', [False, True])
async def test_inbound_always_starts_new_conversation(monkeypatch, stale_attachment):
    from app.db import queries, session as db_session
    from app.db.models import Call

    runtime = TelephonyRuntime()
    call = session()
    call.organization_id = uuid.uuid4()
    call.system_user_id = uuid.uuid4()
    call.caller = '+15551234567'
    previous_id = uuid.uuid4()
    fresh_id = uuid.uuid4()
    call.conversation_id = previous_id if stale_attachment else None
    call.history = [{'role': 'assistant', 'content': '¿Quieres continuar con la reserva?'}]
    conversation = SimpleNamespace(id=fresh_id)
    customer = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    db.add = MagicMock()
    db.scalar.return_value = customer
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_session, 'get_session_factory', lambda: factory)
    create = AsyncMock(return_value=conversation)
    monkeypatch.setattr(queries, 'create_conversation', create)

    await runtime._persist_call(call)

    assert call.conversation_id == fresh_id
    assert call.history == []
    create.assert_awaited_once()
    # No ChannelBinding/phone lookup is allowed to choose an existing thread.
    assert db.scalar.await_count == 1
    db.scalars.assert_not_awaited()
    persisted_call = next(args.args[0] for args in db.add.call_args_list if isinstance(args.args[0], Call))
    assert persisted_call.conversation_id == call.conversation_id
    db.commit.assert_awaited_once()


def test_phrase_pools_avoid_immediate_repetition():
    for pool in (HOLDING, RECOVERY, SILENCE):
        assert len(set(pool)) >= 3
        assert pick(pool, pool[0]) != pool[0]


@pytest.mark.asyncio
async def test_fresh_call_onboarding_introduces_role_and_asks_one_question(monkeypatch):
    runtime = TelephonyRuntime()
    call = session()
    call.websocket = AsyncMock()
    speak = AsyncMock()
    monkeypatch.setattr(runtime_module, '_speak', speak)
    await runtime._greet(call)
    text = speak.call_args.args[2]
    assert 'asistente del restaurante' in text
    assert text.count('¿') == 1
    assert '¿Quieres hacer una reserva?' in text
    assert 'continuar' not in text
    assert call.history == [{'role': 'assistant', 'content': text}]


@pytest.mark.asyncio
async def test_connect_greets_even_when_caller_has_already_started_speaking(monkeypatch):
    runtime = TelephonyRuntime()
    runtime.enable_voice = True
    call = session()
    call.websocket = AsyncMock()
    call.first_voice_at = time.monotonic()
    monkeypatch.setattr(runtime_module, '_speak', AsyncMock())
    await runtime._on_media_event(call, {'event': 'start', 'start': {}})
    await asyncio.wait_for(call.greet_task, timeout=1)
    assert call.greeted
    assert call.history[0]['content'].startswith(('Buenos', 'Buenas'))
    call.silence_task.cancel()
    await asyncio.gather(call.silence_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_caller_speech_is_fed_to_stt_while_initial_greeting_speaks(monkeypatch):
    runtime = TelephonyRuntime()
    runtime.enable_voice = True
    call = session()
    call.stt = object()
    greeting = asyncio.create_task(asyncio.sleep(60))
    call.greet_task = greeting
    call.turn_task = greeting
    feed = AsyncMock(return_value=('', False))
    monkeypatch.setattr(runtime, '_feed_stt', feed)
    pcm = b'\xd0\x07' * 160
    try:
        await runtime._on_customer_pcm(call, pcm)
        feed.assert_awaited_once_with(call, pcm)
        assert not greeting.cancelled()
    finally:
        greeting.cancel()
        await asyncio.gather(greeting, return_exceptions=True)


@pytest.mark.asyncio
async def test_holding_generator_cancellation_closes_pending_turn():
    started = asyncio.Event()
    closed = asyncio.Event()

    async def generate():
        try:
            started.set()
            await asyncio.Event().wait()
            yield 'done', {}
        finally:
            closed.set()

    generator = generate()
    waiting = _with_holding(generator, session(), None)
    pending = asyncio.create_task(anext(waiting))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await waiting.aclose()
    await generator.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_holding_keeps_turn_context_across_all_generator_events():
    turn: ContextVar[str | None] = ContextVar('test_turn', default=None)

    async def generate():
        token = turn.set('same-turn')
        try:
            yield 'tool.started', {}
            assert turn.get() == 'same-turn'
            yield 'token', {'text': 'Gracias, Camilo. ¿En qué puedo ayudarte?'}
            assert turn.get() == 'same-turn'
            yield 'done', {}
        finally:
            turn.reset(token)

    events = [event async for event in _with_holding(generate(), session(), None)]
    assert [kind for kind, _ in events] == ['tool.started', 'token', 'done']
    assert turn.get() is None


@pytest.mark.asyncio
async def test_holding_closes_under_backpressure_without_deadlock():
    closed = asyncio.Event()

    async def generate():
        try:
            for index in range(100):
                yield 'token', {'text': str(index)}
        finally:
            closed.set()

    waiting = _with_holding(generate(), session(), None)
    await anext(waiting)
    await asyncio.sleep(0)
    await asyncio.wait_for(waiting.aclose(), timeout=1)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_reconnected_call_resumes_history_without_new_name_request(monkeypatch):
    runtime = TelephonyRuntime()
    call = session()
    call.websocket = AsyncMock()
    call.history = [{'role': 'user', 'content': 'Me llamo Camilo, reserva para cuatro.'}]
    resume = AsyncMock()
    monkeypatch.setattr(runtime, '_start_turn', resume)
    await runtime._greet(call)
    assert call.greeted
    resume.assert_awaited_once()
    assert 'Retoma' in resume.call_args.args[1]


@pytest.mark.asyncio
async def test_idle_presence_check_uses_pool_and_resets_timer(monkeypatch):
    runtime = TelephonyRuntime()
    call = session()
    call.websocket = AsyncMock()
    call.greeted = True
    call.idle_since = time.monotonic() - 11
    original_sleep = asyncio.sleep
    ticks = 0

    async def tick(_):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            call.closed = True
        await original_sleep(0)

    speak = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, 'sleep', tick)
    monkeypatch.setattr(runtime_module, '_speak', speak)
    await runtime._watch_silence(call)
    await call.turn_task
    assert speak.call_args.args[2] in SILENCE
    assert call.history[-1]['content'] in SILENCE
    assert time.monotonic() - call.idle_since < 1
