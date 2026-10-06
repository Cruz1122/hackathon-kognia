from types import SimpleNamespace
import uuid

from app.agent.policy import (
    Turn, active_turn, apply_observations, authorize, claims_callback,
)
from app.agent.state import AgentState, Fact, Signal


def state():
    return AgentState(conversation_id=str(uuid.uuid4()), organization_id=str(uuid.uuid4()))


def _verified_callback(memory, turn_id='current'):
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(value='explicit', confidence=.99, turn_id=turn_id, model='test')


def test_callback_tool_only_authorizes_current_turn_with_verified_destination():
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(value='explicit', confidence=1, turn_id='current', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'Vuelve a llamarme')
        assert authorize(memory, 'call_customer', {})
        assert memory.callback_authorized_turn_id == 'current'
        assert memory.pending is None
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


def test_explicit_callback_is_authorized_without_model_tool_call():
    # Regression: a failed previous call_customer must not make the model
    # "explain" an error instead of retrying. Policy authorizes the current
    # explicit callback so the runtime executes it deterministically.
    memory = state()
    _verified_callback(memory)
    memory.signals['frustration'] = Signal(value='high', confidence=.83, turn_id='current', model='test')
    memory.tool_history = [{'tool': 'call_customer', 'ok': False, 'error': 'TOOL_EXECUTION_ERROR',
                            'turn_id': 'previous', 'arguments': {}}]
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'Lláame pls')
        assert memory.pending is None
        assert memory.authorized is None
        assert memory.callback_authorized_turn_id == 'current'
    finally:
        active_turn.reset(token)


def test_explicit_callback_requires_verified_destination():
    memory = state()
    memory.signals['callback_request'] = Signal(value='explicit', confidence=.99, turn_id='current', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'Lláame pls')
        assert memory.authorized is None
    finally:
        active_turn.reset(token)


def test_stale_callback_verdict_cannot_authorize_current_turn():
    memory = state()
    _verified_callback(memory, turn_id='old')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'Llámame')
        assert memory.authorized is None
    finally:
        active_turn.reset(token)


def test_callback_does_not_override_an_authorized_confirmation():
    memory = state()
    _verified_callback(memory)
    authorize(memory, 'create_booking', {'party_size': 4})
    memory.pending.presented = True
    memory.signals['confirmation'] = Signal(value='explicit', confidence=.99, turn_id='current', model='test')
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'sí, confirma y llámame')
        assert memory.pending.tool == 'create_booking'
        assert memory.authorized == memory.pending.fingerprint
    finally:
        active_turn.reset(token)


def test_non_explicit_semantic_verdict_never_authorizes_from_probability():
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(
        value='not_requested', confidence=.4, turn_id='current', model='test',
        probabilities={'explicit': 0.6, 'not_requested': 0.39, 'unknown': 0.01})
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'Sí llámame')
        assert memory.authorized is None
    finally:
        active_turn.reset(token)


def test_context_noise_below_threshold_does_not_authorize():
    # "No, estoy bien... te quiero mucho" after a call scored explicit=0.37.
    memory = state()
    memory.facts['customer.phone'] = Fact(value='+15551234567', source='verified_channel_binding')
    memory.signals['callback_request'] = Signal(
        value='not_requested', confidence=.58, turn_id='current', model='test',
        probabilities={'explicit': 0.37, 'not_requested': 0.62})
    token = active_turn.set(Turn(SimpleNamespace(state=memory), 'current'))
    try:
        apply_observations(memory, 'No, estoy bien, gracias. Solo te quería decir que te quiero mucho')
        assert memory.authorized is None
    finally:
        active_turn.reset(token)


def test_claims_callback_only_matches_real_call_claims():
    assert claims_callback('En este momento te estoy llamando al número registrado.')
    assert claims_callback('Ya tienes una llamada en curso con nosotros.')
    assert not claims_callback('¿Confirmas que te llame al teléfono de esta conversación?')
