import uuid

from app.agent.dialogue import Plan, Slots, apply, next_question
from app.agent.state import AgentState, Fact, Signal
from app.agent.policy import Turn, active_turn, authorize
from types import SimpleNamespace


def state():
    return AgentState(conversation_id=str(uuid.uuid4()), organization_id=str(uuid.uuid4()))


def test_steps_collect_date_time_party_and_name_one_at_a_time():
    memory = state()
    assert next_question(memory) == '¿Para qué día quieres la reserva?'
    apply(memory, Plan(action='booking', updates=Slots(date='2026-10-05')), 't1')
    assert next_question(memory) == '¿A qué hora quieres la reserva?'
    apply(memory, Plan(action='booking', updates=Slots(time_hint='ocho')), 't2')
    assert next_question(memory) == '¿A las ocho de la mañana o de la noche?'
    apply(memory, Plan(action='booking', updates=Slots(time='08:00', time_period='morning')), 't3')
    assert next_question(memory) == '¿Para cuántas personas será la mesa?'
    apply(memory, Plan(action='booking', updates=Slots(party_size=8)), 't4')
    assert next_question(memory) == '¿A nombre de quién dejamos la reserva?'
    apply(memory, Plan(action='booking', updates=Slots(customer_name='Camilo')), 't5')
    assert next_question(memory) is None


def test_garbled_fragment_preserves_already_known_date_and_hour_hint():
    memory = state()
    apply(memory, Plan(action='booking', updates=Slots(date='2026-10-05', time_hint='ocho')), 't1')
    original = dict(memory.booking_slots)
    apply(memory, Plan(action='unclear'), 't2')
    assert memory.booking_slots == original
    assert next_question(memory).count('¿') == 1
    assert 'mañana o de la noche' in next_question(memory)


def test_ambiguous_period_never_promotes_guessed_time():
    memory = state()
    apply(memory, Plan(action='booking', updates=Slots(time='08:00', time_hint='ocho')), 't1')
    assert 'time' not in memory.booking_slots


def test_simplification_complaint_does_not_change_known_schedule():
    memory = state()
    apply(memory, Plan(action='booking', updates=Slots(date='2026-10-05', time='08:00', time_period='morning',
        party_size=8, customer_name='Camilo')), 't1')
    original = dict(memory.booking_slots)
    apply(memory, Plan(action='booking', simplify=True), 't2')
    assert memory.booking_slots == original


def test_callback_request_only_authorizes_current_turn_verified_callback():
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(value='explicit', confidence=1, turn_id='current', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        assert authorize(memory, 'call_customer', {})
        assert not authorize(memory, 'create_booking', {'party_size': 8})
        assert memory.pending.presented is False
    finally:
        active_turn.reset(token)


def test_stale_callback_request_cannot_authorize_new_turn():
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(value='explicit', confidence=1, turn_id='old', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        assert not authorize(memory, 'call_customer', {})
    finally:
        active_turn.reset(token)


def test_unfinished_condition_change_withdraws_old_proposal_even_when_jev_says_explicit():
    memory = state()
    memory.booking_slots = {'date': '2026-10-05', 'time': '19:00', 'time_period': 'night', 'party_size': 4, 'customer_name': 'Camilo'}
    authorize(memory, 'create_booking', {key: value for key, value in memory.booking_slots.items() if key != 'time_period'})
    memory.pending.presented = True
    memory.signals['confirmation'] = Signal(value='explicit', confidence=1, turn_id='current', model='test')
    apply(memory, Plan(action='booking', clear_fields=['time'], confirmation='uncertain'), 'current')
    assert memory.pending is None
    assert memory.signals['confirmation'].value == 'uncertain'
    assert next_question(memory) == '¿A qué hora quieres la reserva?'
