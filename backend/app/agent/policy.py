from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from .state import AgentState, Proposal, fingerprint, now

if TYPE_CHECKING:
    from .store import StateSession


_CLAIMS_CALLBACK = re.compile(
    r'\b(?:te estoy llamando|estoy llamando|te llamo|voy a llamarte|'
    r'te marco|estoy marcando|llamada en curso|realizando la llamada)\b'
)


def claims_callback(text: str) -> bool:
    """True when a draft claims a call is happening or about to happen."""
    # This is only an output-claim guard, never an authorization decision.
    normalized = unicodedata.normalize('NFD', (text or '').lower())
    folded = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')
    return bool(_CLAIMS_CALLBACK.search(folded))


def apply_observations(state: AgentState, prompt: str) -> None:
    state.authorized = None
    state.callback_authorized_turn_id = None
    human = state.signals.get('human')
    if human and human.value == 'requested' and human.confidence >= .8:
        state.handoff_requested = True
        state.action('human_requested')
    intent = state.signals.get('intent')
    confirmation = state.signals.get('confirmation')
    frustration = state.signals.get('frustration')
    explicit_confirmation = confirmation is not None and confirmation.value == 'explicit'
    high_frustration = frustration is not None and frustration.value in {'high', 'very_high'}
    # A cancellation always invalidates the old proposal. A "correct" intent only
    # does so when the turn is not an explicit confirmation: JEV labels broad
    # affirmations such as "que sí" as corrections at low confidence, and that
    # must not discard a scoped "yes" to the exact presented proposal. Doing so
    # withdrew the proposal and made the agent repeat the same confirmation
    # question forever.
    conflicts_with_confirmation = intent is not None and (
        intent.value == 'cancel'
        or (intent.value == 'correct' and not explicit_confirmation)
    )
    # Frustration is conversational context, not a revocation of consent. The
    # semantic confirmation verdict already distinguishes an angry insult from
    # an explicit "sí, la confirmo". Blocking the latter merely because the
    # customer is still upset caused a real confirmed reservation to loop.
    explicit = explicit_confirmation and not conflicts_with_confirmation
    if conflicts_with_confirmation or (confirmation and confirmation.value == 'rejected'):
        if state.pending:
            state.action('proposal_withdrawn', proposal=state.pending.fingerprint,
                         reason='customer_rejected_or_changed')
        state.pending = None
        state.phase = 'understanding'
    elif high_frustration and state.pending:
        # Tension is not a cancellation and never erases the task. Whether this
        # same utterance confirms the proposal is decided by the dedicated
        # semantic confirmation signal, not by the customer's tone.
        state.action('frustration_observed', proposal=state.pending.fingerprint)
    pending = state.pending
    if (not state.handoff_requested and pending and pending.presented
            and now() - pending.created_at < timedelta(minutes=15)
            and explicit and not conflicts_with_confirmation):
        state.authorized = pending.fingerprint
        state.action('explicit_confirmation_received', proposal=pending.fingerprint)
    turn = active_turn.get()
    if turn is not None and state.authorized is None:
        # A fresh explicit callback request is a policy decision, not a model
        # decision. Relying on the LLM to emit call_customer is unreliable when a
        # previous failed attempt is still visible in the tool results.
        authorize_callback_request(state, turn.turn_id)


def authorize_callback_request(state: AgentState, turn_id: str) -> bool:
    """Authorize call_customer only for the current verified explicit callback turn."""
    if state.handoff_requested:
        return False
    callback_request = state.signals.get('callback_request')
    verified_phone = state.facts.get('customer.phone')
    # This signal is produced by a dedicated semantic evaluation that sees only
    # the current message. The conversation-level evaluator is never an
    # authorization source.
    if (callback_request is None or callback_request.turn_id != turn_id
            or callback_request.value != 'explicit'):
        return False
    if not verified_phone or verified_phone.source != 'verified_channel_binding':
        return False
    state.callback_authorized_turn_id = turn_id
    state.action('callback_requested_by_customer', turn_id=turn_id, source='isolated_semantic_verdict')
    return True


def authorize(state: AgentState, name: str, arguments: dict) -> bool:
    key = fingerprint(name, arguments)
    if state.handoff_requested:
        return False
    turn = active_turn.get()
    if name == 'call_customer' and not arguments and turn is not None:
        return state.callback_authorized_turn_id == turn.turn_id or authorize_callback_request(state, turn.turn_id)
    if state.authorized == key and state.pending and state.pending.fingerprint == key:
        return True
    if state.pending is None or state.pending.fingerprint != key:
        state.pending = Proposal(tool=name, arguments=arguments, fingerprint=key)
        state.authorized = None
        state.phase = 'confirming'
        state.goal = name
        state.action('action_proposed', tool=name, proposal=key)
    return False


@dataclass
class Turn:
    store: StateSession
    turn_id: str


active_turn: ContextVar[Turn | None] = ContextVar('active_agent_turn', default=None)
