"""Continuation message: LLM recap with a safe fallback and Jev-guided tone."""
from types import SimpleNamespace

import pytest

from app.agent.state import AgentState, Signal
from app.config import ModelConfig, Provider
from app.db.models import MessageRole
from app.whatsapp import service
from app.whatsapp.service import (
    DEFAULT_CONTINUATION_TEXT,
    MAX_CONTINUATION_CHARS,
    clean_continuation,
    continuation_message,
    continuation_tone,
    render_transcript,
)

CONFIG = ModelConfig(provider=Provider.OPENAI, model='test', api_key='key', base_url='http://test.invalid')


class Row:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, rows=None, snapshot=None):
        self.rows = rows or []
        self.snapshot = snapshot

    async def scalars(self, statement):
        return FakeResult(self.rows)

    async def get(self, model, key):
        return self.snapshot


class FakeLLM:
    def __init__(self, text='Mensaje generado', error=None):
        self.text = text
        self.error = error
        self.prompts = []

    async def stream(self, config, prompt, *, messages=None, tools=None, client=None):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        yield 'token', {'text': self.text}
        yield 'done', {}


@pytest.fixture
def whatsapp_env(monkeypatch):
    for name, value in [('WHATSAPP_ACCESS_TOKEN', 'token'), ('WHATSAPP_APP_SECRET', 'secret'),
                        ('WHATSAPP_VERIFY_TOKEN', 'verify'), ('WHATSAPP_PHONE_NUMBER_ID', '123')]:
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)


def _state(**signals) -> AgentState:
    state = AgentState(conversation_id='c', organization_id='o')
    for key, value in signals.items():
        state.signals[key] = Signal(value=value, confidence=.9, turn_id='t', model='fake')
    return state


def _snapshot(state: AgentState) -> SimpleNamespace:
    return SimpleNamespace(data=state.model_dump(mode='json'))


def test_clean_continuation_collapses_whitespace():
    assert clean_continuation('  Hola\n\n  mundo  ') == 'Hola mundo'


def test_render_transcript_labels_roles_and_truncates():
    text = render_transcript([Row(MessageRole.USER, ' quiero  reservar '), Row(MessageRole.ASSISTANT, 'claro')])
    assert text == 'Cliente: quiero reservar\nAsesor: claro'


def test_continuation_tone_reads_jev_signals():
    assert 'acknowledge the specific mistake' in continuation_tone(_state(frustration='high'))
    assert 'at most two short sentences' in continuation_tone(_state(frustration='very_high'))
    assert 'frustrado' not in continuation_tone(_state(frustration='high'))
    assert 'persona' not in continuation_tone(_state(human='requested'))
    assert 'concrete alternative' in continuation_tone(_state(satisfaction='low'))
    assert continuation_tone(_state(satisfaction='very_high')) == ''
    assert continuation_tone(None) == ''


def test_within_service_window_bounds():
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    assert service.within_service_window(SimpleNamespace(last_inbound_at=now - timedelta(hours=1)), now)  # type: ignore[arg-type]
    assert not service.within_service_window(SimpleNamespace(last_inbound_at=now - timedelta(hours=25)), now)  # type: ignore[arg-type]
    assert not service.within_service_window(SimpleNamespace(last_inbound_at=None), now)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_continuation_uses_llm_recap_with_tone(whatsapp_env):
    llm = FakeLLM(text='Lamento el corte. Hablamos de tu reserva; ¿retomamos?')
    state = _state(frustration='high', human='requested')
    db = FakeDB(rows=[Row(MessageRole.USER, 'quiero reservar para Juan'),
                      Row(MessageRole.ASSISTANT, '¿para qué fecha?')], snapshot=_snapshot(state))
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=llm, chain=[CONFIG])
    assert text == 'Lamento el corte. Hablamos de tu reserva; ¿retomamos?'
    prompt = llm.prompts[0]
    assert 'quiero reservar para Juan' in prompt and 'acknowledge the specific mistake' in prompt
    assert 'persona' not in prompt
    assert 'El cliente se mostró frustrado' not in prompt


@pytest.mark.asyncio
async def test_continuation_falls_back_without_transcript(whatsapp_env):
    llm = FakeLLM()
    text = await continuation_message('c', 'o', 'call-1', db=FakeDB(rows=[]), llm=llm, chain=[CONFIG])
    assert text == DEFAULT_CONTINUATION_TEXT
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_continuation_falls_back_on_provider_error(whatsapp_env):
    llm = FakeLLM(error=RuntimeError('provider down'))
    db = FakeDB(rows=[Row(MessageRole.USER, 'hola')], snapshot=None)
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=llm, chain=[CONFIG])
    assert text == DEFAULT_CONTINUATION_TEXT


@pytest.mark.asyncio
async def test_continuation_falls_back_when_too_long(whatsapp_env):
    llm = FakeLLM(text='x' * (MAX_CONTINUATION_CHARS + 1))
    db = FakeDB(rows=[Row(MessageRole.USER, 'hola')], snapshot=None)
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=llm, chain=[CONFIG])
    assert text == DEFAULT_CONTINUATION_TEXT


@pytest.mark.asyncio
async def test_continuation_is_not_blocked_by_integrity(whatsapp_env, monkeypatch):
    # The recap is a fallback only when the LLM fails; an integrity verdict never overrides it.
    monkeypatch.setenv('TYPESAFE_API_KEY', 'present')
    from app.agent import jev

    async def unsupported(state, draft, turn_id, knowledge=None):
        return Signal(value='unsupported', confidence=.9, turn_id=turn_id, model='fake')

    monkeypatch.setattr(jev, 'integrity', unsupported)
    db = FakeDB(rows=[Row(MessageRole.USER, 'hola')], snapshot=_snapshot(_state()))
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=FakeLLM(text='Resumen real'), chain=[CONFIG])
    assert text == 'Resumen real'


@pytest.mark.asyncio
async def test_continuation_keeps_supported_integrity(whatsapp_env, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'present')
    from app.agent import jev

    async def supported(state, draft, turn_id, knowledge=None):
        return Signal(value='supported', confidence=.9, turn_id=turn_id, model='fake')

    monkeypatch.setattr(jev, 'integrity', supported)
    db = FakeDB(rows=[Row(MessageRole.USER, 'hola')], snapshot=_snapshot(_state()))
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=FakeLLM(text='Resumen ok'), chain=[CONFIG])
    assert text == 'Resumen ok'


@pytest.mark.asyncio
async def test_continuation_skips_providers_without_api_key(whatsapp_env):
    llm = FakeLLM(text='generado')
    no_key = ModelConfig(provider=Provider.GEMINI, model='x', api_key='', base_url='http://test.invalid')
    db = FakeDB(rows=[Row(MessageRole.USER, 'hola')], snapshot=None)
    text = await continuation_message('c', 'o', 'call-1', db=db, llm=llm, chain=[no_key, CONFIG])
    assert text == 'generado'
    assert len(llm.prompts) == 1
