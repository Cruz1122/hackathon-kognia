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
# Folded text has no accents. "urgencias" and a strong pain without danger signs stay out.
_EMERGENCY = re.compile(
    r'(?:dolor en el pecho|dolor de pecho|me duele el pecho|'
    r'no puedo respirar|no puede respirar|\bse ahoga\b|\bme ahogo\b|'
    r'\binconsciente\b|se desmayo|'
    r'\bconvulsion(?:es|ando)?\b|'
    r'sangrado abundante|'
    r'\binfarto\b|\bderrame\b|'
    r'\bsobredosis\b|\benvenen\w*|\bintoxicad\w*|'
    r'\bsuicid\w*|quiero morir|'
    r'accidente grave)'
)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize('NFD', (text or '').lower())
    return ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')


def claims_callback(text: str) -> bool:
    """True when a draft claims a call is happening or about to happen."""
    # This is only an output-claim guard, never an authorization decision.
    return bool(_CLAIMS_CALLBACK.search(_fold(text)))


def mentions_emergency(text: str) -> bool:
    """True when the user's own words describe a possible emergency right now."""
    return bool(_EMERGENCY.search(_fold(text)))


def apply_observations(state: AgentState, prompt: str) -> None:
    state.authorized = None
    state.callback_authorized_turn_id = None
    confirmation = state.signals.get('confirmation')
    frustration = state.signals.get('frustration')
    explicit_confirmation = confirmation is not None and confirmation.value == 'explicit'
    high_frustration = frustration is not None and frustration.value in {'high', 'very_high'}
    # Frustration is conversational context, not a revocation of consent.
    if confirmation and confirmation.value == 'rejected':
        if state.pending:
            state.action('proposal_withdrawn', proposal=state.pending.fingerprint,
                         reason='customer_rejected_or_changed')
        state.pending = None
        state.phase = 'understanding'
    elif high_frustration and state.pending:
        state.action('frustration_observed', proposal=state.pending.fingerprint)
    pending = state.pending
    if (not state.handoff_requested and pending and pending.presented
            and now() - pending.created_at < timedelta(minutes=15)
            and explicit_confirmation):
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
