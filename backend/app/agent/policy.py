from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from .state import AgentState, Proposal, fingerprint, now

if TYPE_CHECKING:
    from .store import StateSession

# Deterministic consent anchor. Jev corroborates the interpretation but never authorizes alone.
CONSENT_PHRASES = {
    'sí', 'si', 'sí confirmo', 'si confirmo', 'confirmo', 'confirmo por favor',
    'confirmo la reserva', 'confirmo la llamada', 'yes', 'i confirm',
}
NON_WORD = re.compile(r'[^\wáéíóúüñ]+', re.I)


def is_explicit_consent(text: str) -> bool:
    normalized = NON_WORD.sub(' ', text.strip().lower()).strip()
    return normalized in CONSENT_PHRASES


def apply_observations(state: AgentState, prompt: str) -> None:
    state.authorized = None
    human = state.signals.get('human')
    if human and human.value == 'requested' and human.confidence >= .8:
        state.handoff_requested = True
        state.action('human_requested')
    intent = state.signals.get('intent')
    if intent and intent.value in {'correct', 'cancel'} and intent.confidence >= .8:
        state.pending = None
        state.phase = 'understanding'
    confirmation = state.signals.get('confirmation')
    pending = state.pending
    if (not state.handoff_requested and pending and pending.presented
            and now() - pending.created_at < timedelta(minutes=15)
            and is_explicit_consent(prompt)
            # The calibrated confidence of a Choice is not a reliable threshold (real
            # "confirmo" returns explicit at ~0.44). Consent is anchored on the exact
            # deterministic phrase and Jev must not disagree; probability never authorizes alone.
            and confirmation and confirmation.value == 'explicit'):
        state.authorized = pending.fingerprint
        state.action('explicit_confirmation_received', proposal=pending.fingerprint)


def authorize(state: AgentState, name: str, arguments: dict) -> bool:
    key = fingerprint(name, arguments)
    if state.handoff_requested:
        return False
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
