"""Real LLM/Jev text-turn benchmark. Requires a dedicated migrated benchmark DB.

Only outbound PSTN effects are mocked; no real calls or WhatsApp sends are made.
Run from backend: .venv/bin/python scripts/benchmark_agent.py --database-url ... --output ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

from scripts.agent_stt_cases import SCENARIOS as STT_SCENARIOS, speech
from scripts.agent_guided_cases import SCENARIOS as GUIDED_SCENARIOS

TYPING_SCENARIOS = [
    ('intro_booking', 'booking', ['ola soi Camilo', 'kiero una resurva para cuatro mañana a las siete de la noche', 'si confiermo']),
    ('noisy_party', 'booking', ['Soy Camilo, quiero reservar para mañana a las siete de la noche, cuatro perzonas', 'sii porfa asla']),
    ('split_details', 'booking', ['Soy Camilo y necesito una reserva', 'Mañana a las siete de la noche', 'cuatro pirodos', 'Cuatro personas', 'Sí, confirmo']),
    ('changed_conditions', 'changed', ['Soy Camilo, reserva mañana a las siete de la noche para cuatro', 'si pero mejol a las ocho de la noche', 'dale confimalo']),
    ('ambiguous_consent', 'no_write', ['Soy Camilo, reserva mañana a las siete de la noche para cuatro', 'tal ves si alcanzo', 'No, mejor no la hagas']),
    ('cancel_proposal', 'cancel', ['Soy Camilo, reserva mañana a las siete de la noche para cuatro', 'no olbidalo mejor no', 'ola']),
    ('noisy_callback', 'callback', ['yamame porfa', 'sii yamame']),
    ('unavailable_group', 'unavailable', ['Soy Camilo quiero mesa para dose personas mañana a las siete de la noche', 'Doce personas', '¿Qué opción tienes para nosotros?']),
    ('human_request', 'human', ['quiero ablar con un asesor umano', 'Sí confirma una reserva para cuatro']),
    ('reported_stt', 'booking', ['Hola me llamo Camilo', 'Si me llevo camino', 'Necesito una reserva para cuatro mañana a las siete de la noche', 'Sí, confirmo']),
]


async def run_scenario(spec, org_id, user_id):
    from sqlalchemy import select
    from app.agent.runtime import stateful_stream
    from app.agent.store import mark_presented
    from app.db.models import AgentOperation, AgentSnapshot, ChannelBinding, Conversation, Customer, Message, MessageRole
    from app.db.session import get_session_factory
    from app.agent.tools.contracts import ToolContext
    from app.features.agent.service import _generate

    name, goal, prompts = spec
    prompts = list(prompts)
    scripted_turns = len(prompts)
    async with get_session_factory()() as db:
        customer = Customer(organization_id=org_id, phone='+1555' + str(uuid.uuid4().int)[:7])
        db.add(customer)
        await db.flush()
        conversation = Conversation(organization_id=org_id, created_by=user_id, customer_id=customer.id,
                                    channel='voice', status='open')
        db.add(conversation)
        await db.flush()
        db.add(ChannelBinding(organization_id=org_id, conversation_id=conversation.id,
            phone_number_id='benchmark-only', phone=customer.phone, wa_id=customer.phone[1:]))
        await db.commit()
        cid = conversation.id
    turns = []
    for index, stimulus in enumerate(prompts):
        stimulus = stimulus if isinstance(stimulus, dict) else speech(stimulus, error='typing_fixture')
        prompt = stimulus['transcript']
        channel = stimulus.get('channel', 'voice')
        started = time.monotonic()
        events = [event async for event in stateful_stream(prompt, messages=None, llm=None,
            tool_context=ToolContext(f'{cid}-{index}', conversation_id=str(cid), organization_id=str(org_id),
                                     user_id=str(user_id), channel=channel), generate=_generate)]
        reply = ''.join(data['text'] for kind, data in events if kind == 'token')
        done = next((data for kind, data in events if kind == 'done'), {})
        turns.append({**stimulus, 'user': prompt, 'agent': reply, 'seconds': round(time.monotonic() - started, 2),
            'done': done, 'tools': [data for kind, data in events if kind == 'tool.completed']})
        async with get_session_factory()() as db:
            db.add_all([Message(conversation_id=cid, role=MessageRole.USER, content=prompt, channel=channel),
                        Message(conversation_id=cid, role=MessageRole.ASSISTANT, content=reply, channel=channel)])
            await db.commit()
        if done.get('proposal_id'):
            await mark_presented(str(org_id), str(cid), done['proposal_id'])
        async with get_session_factory()() as db:
            snapshot = await db.get(AgentSnapshot, cid)
            assert snapshot is not None
            turns[-1]['pending'] = snapshot.data.get('pending')
            turns[-1]['confirmation'] = snapshot.data.get('signals', {}).get('confirmation')
            turns[-1]['booking_slots'] = snapshot.data.get('booking_slots', {})
        if goal in {'callback', 'reported_callback'} and any(item['tool'] == 'call_customer' and item['ok']
                and item['turn_id'] == f'{cid}-{index}' for item in snapshot.data.get('tool_history', [])):
            print(f'{name} turn {index + 1} STT: {prompt}\nAgent: {reply}', flush=True)
            break
        # A simulated customer responds to a genuine pending proposal, rather
        # than abandoning the call at an arbitrary fixed transcript length.
        if index == len(prompts) - 1 and goal in {'booking', 'changed', 'callback'} and len(prompts) < scripted_turns + 2:
            if snapshot.data.get('pending'):
                prompts.append(speech('Sí, adelante con esos datos.'))
        print(f'{name} turn {index + 1} STT: {prompt}\nAgent: {reply}', flush=True)
    async with get_session_factory()() as db:
        operations = (await db.scalars(select(AgentOperation).where(AgentOperation.conversation_id == cid,
            AgentOperation.tool != 'agent_turn'))).all()
        snapshot = await db.get(AgentSnapshot, cid)
        assert snapshot is not None
    writes = [{'tool': item.tool, 'status': item.status, 'arguments': item.arguments} for item in operations]
    bookings = [item for item in writes if item['tool'] == 'create_booking' and item['status'] == 'succeeded']
    callbacks = [item for item in writes if item['tool'] == 'call_customer' and item['status'] == 'succeeded']
    clean_replies = all(turn['agent'] and not any(marker in turn['agent'].lower() for marker in
        ['no pude completar ese paso', 'problema al procesar', 'te sientes frustrado', 'estás frustrado',
         'qué te gustaría que revisemos primero', 'para seguir, ¿qué necesitas gestionar?',
         'un momento, por favor.']) for turn in turns)
    model_generated_turns = sum(turn['done'].get('provider') in {'openai', 'gemini', 'openrouter', 'groq'}
                                for turn in turns)
    policy_turns = sum(turn['done'].get('provider') == 'policy' for turn in turns)
    interpreted_turns = sum(turn['done'].get('interpretation_provider') in {'openai', 'gemini', 'openrouter', 'groq'}
                            for turn in turns)
    real_models = bool(turns) and model_generated_turns == len(turns)
    completed = False
    if goal in {'booking', 'changed', 'booking_morning'}:
        target_time = '08:00' if goal == 'booking_morning' else '20:00' if goal == 'changed' else '19:00'
        completed = (len(bookings) == 1 and bookings[0]['arguments']['party_size'] == (8 if goal == 'booking_morning' else 4)
            and bookings[0]['arguments']['time'] == target_time
            and bookings[0]['arguments']['customer_name'] == 'Camilo')
    elif goal in {'callback', 'reported_callback'}:
        completed = len(callbacks) == 1
        if goal == 'reported_callback':
            slots = snapshot.data.get('booking_slots', {})
            completed = completed and not bookings and slots.get('time') == '08:00' and slots.get('party_size') == 8 and slots.get('customer_name') == 'Camilo'
    elif goal == 'human':
        completed = snapshot.data.get('handoff_requested') is True and not writes
    elif goal == 'unavailable':
        completed = not writes and snapshot.data.get('pending') is None and any(
            item.get('tool') == 'check_availability' and item.get('arguments', {}).get('party_size') == 12
            for item in snapshot.data.get('tool_history', []))
        # The documented group-size limit can be explained without a redundant lookup.
        completed = completed or (not writes and snapshot.data.get('pending') is None and any(
            ('10' in turn['agent'] or 'diez' in turn['agent']) and ('12' in turn['agent'] or 'doce' in turn['agent'])
            for turn in turns[1:]))
    if name in {'reported_stt', 'stt_known_name'}:
        clean_replies = clean_replies and not any(word in turns[1]['agent'].lower() for word in ['llame', 'llamen', 'llamar', 'número específico'])
    if goal not in {'booking', 'changed', 'booking_morning', 'callback', 'reported_callback', 'human', 'unavailable'}:
        completed = not writes and (goal != 'cancel' or snapshot.data.get('pending') is None)
    return {'id': name, 'goal': goal, 'objective_pass': completed, 'naturalness_gate': clean_replies,
        'contains_irrecoverable_error': any(turn['recoverability'] == 'irrecoverable_from_text' for turn in turns),
        'extra_confirmation_turns': len(prompts) - scripted_turns,
        'model_generated_turns': model_generated_turns, 'policy_turns': policy_turns,
        'interpreted_turns': interpreted_turns,
        'one_question_gate': all(turn['agent'].count('¿') <= 1 for turn in turns),
        'real_provider': real_models, 'turns': turns, 'writes': writes, 'state': snapshot.data}


async def main(args):
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    from sqlalchemy.engine import make_url
    if 'benchmark' not in (make_url(args.database_url).database or ''):
        raise ValueError('Refusing non-benchmark database')
    os.environ['DATABASE_URL'] = args.database_url
    os.environ['TELNYX_ENABLED'] = 'true'
    from app.db.models import Organization, User, UserRole
    from app.db.session import get_session_factory, dispose_engine
    from app.telephony.telnyx_api import TelnyxApi
    async with get_session_factory()() as db:
        org = Organization(name='Agent benchmark', slug='benchmark-' + uuid.uuid4().hex)
        db.add(org)
        await db.flush()
        user = User(organization_id=org.id, email=uuid.uuid4().hex + '@benchmark.invalid',
                    password_hash='not-a-login', role=UserRole.ADMIN)
        db.add(user)
        await db.commit()
        org_id, user_id = org.id, user.id
    dial = AsyncMock(return_value={'call_control_id': 'simulated-control', 'call_leg_id': 'simulated-leg',
                                  'call_session_id': 'simulated-session'})
    results = []
    try:
        with patch.object(TelnyxApi, 'dial', dial):
            scenarios = GUIDED_SCENARIOS if args.suite == 'guided' else STT_SCENARIOS if args.suite == 'stt' else TYPING_SCENARIOS
            for scenario in scenarios:
                results.append(await run_scenario(scenario, org_id, user_id))
                Path(args.output).write_text(json.dumps({'suite': args.suite,
                    'simulation': 'real LLM/Jev, hand-authored transcription errors, mocked PSTN; no acoustic STT run',
                    'objective_scope': 'completion after scripted user clarifications; not immediate semantic recovery',
                    'naturalness_gate_scope': 'lexical regression markers only, not a semantic quality score',
                    'scenarios': results}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"Objectives: {sum(item['objective_pass'] for item in results)}/{len(results)}; "
              f"naturalness gates: {sum(item['naturalness_gate'] for item in results)}/{len(results)}; "
              f"one-question gates: {sum(item['one_question_gate'] for item in results)}/{len(results)}; "
              f"simulated dials={dial.await_count}")
        recoverable = [item for item in results if not item['contains_irrecoverable_error']]
        print(f"Goals after scripted clarification (excluding irreversible loss): "
              f"{sum(item['objective_pass'] for item in recoverable)}/{len(recoverable)}")
    finally:
        await dispose_engine()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--suite', choices=['stt', 'typing', 'guided'], default='stt')
    asyncio.run(main(parser.parse_args()))
