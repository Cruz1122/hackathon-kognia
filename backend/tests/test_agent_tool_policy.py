from types import SimpleNamespace
import uuid

from app.agent.policy import Turn, active_turn, authorize
from app.agent.state import AgentState, Fact, Signal


def state():
    return AgentState(conversation_id=str(uuid.uuid4()), organization_id=str(uuid.uuid4()))


def test_callback_tool_only_authorizes_current_turn_with_verified_destination():
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


def test_stale_callback_intent_cannot_authorize_tool_call():
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(value='explicit', confidence=1, turn_id='old', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        assert not authorize(memory, 'call_customer', {})
    finally:
        active_turn.reset(token)
