from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from .state import AgentState, Proposal, fingerprint, now

if TYPE_CHECKING:
    from .store import StateSession

def apply_observations(state: AgentState, prompt: str) -> None:
    state.authorized = None
    human = state.signals.get('human')
    if human and human.value == 'requested' and human.confidence >= .8:
        state.handoff_requested = True
        state.action('human_requested')
    intent = state.signals.get('intent')
    confirmation = state.signals.get('confirmation')
    explicit = confirmation is not None and confirmation.value == 'explicit'
    # Interpret language with the model, not phrase dictionaries. An explicit
    # verdict about this exact proposal takes precedence over a broad intent label.
    if ((not explicit and intent and intent.value in {'correct', 'cancel'})
            or (confirmation and confirmation.value == 'rejected')):
        if state.pending:
            state.action('proposal_withdrawn', proposal=state.pending.fingerprint)
        state.pending = None
        state.phase = 'understanding'
    pending = state.pending
    if (not state.handoff_requested and pending and pending.presented
            and now() - pending.created_at < timedelta(minutes=15)
            and explicit):
        state.authorized = pending.fingerprint
        state.action('explicit_confirmation_received', proposal=pending.fingerprint)


def authorize(state: AgentState, name: str, arguments: dict) -> bool:
    key = fingerprint(name, arguments)
    if state.handoff_requested:
        return False
    turn = active_turn.get()
    callback_request = state.signals.get('callback_request')
    verified_phone = state.facts.get('customer.phone')
    if (name == 'call_customer' and not arguments and turn is not None
            and callback_request and callback_request.value == 'explicit'
            and callback_request.turn_id == turn.turn_id
            and verified_phone and verified_phone.source == 'verified_channel_binding'):
        if state.pending is None or state.pending.fingerprint != key:
            state.pending = Proposal(tool=name, arguments=arguments, fingerprint=key)
        state.authorized = key
        state.action('callback_requested_by_customer', turn_id=turn.turn_id)
        return True
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
