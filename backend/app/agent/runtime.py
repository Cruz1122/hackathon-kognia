"""One turn coordinator shared by every channel; no unvalidated tokens escape."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from sqlalchemy import select

from . import jev
from .policy import Turn, active_turn, apply_observations
from .state import Fact, fingerprint
from .store import conversation_state

logger = logging.getLogger(__name__)
SAFE_REPLY = 'No puedo verificar la respuesta en este momento. No voy a afirmar que la acción se completó. Podemos intentarlo más tarde o solicitar ayuda humana.'


async def stateful_stream(prompt, *, messages, llm, tool_context, generate):
    context = tool_context
    try:
        async with asyncio.timeout(120):
            async with conversation_state(context.organization_id, context.conversation_id) as store:
                state = store.state
                from ..db.models import AgentOperation, ChannelBinding, Message, MessageRole
                from ..features.agent.service import TOOL_REGISTRY

                binding = await store.db.scalar(select(ChannelBinding).where(
                    ChannelBinding.conversation_id == store.row.conversation_id,
                    ChannelBinding.organization_id == store.row.organization_id))
                if binding:
                    state.facts['customer.phone'] = Fact(value=binding.phone, source='verified_channel_binding')
                turn_key = fingerprint(state.conversation_id, {'turn': context.request_id})
                previous = await store.db.get(AgentOperation, turn_key)
                if previous and previous.status == 'succeeded' and previous.result:
                    yield 'token', {'text': previous.result['text']}
                    yield 'done', {'provider': 'state', 'model': 'replay', 'proposal_id': previous.result.get('proposal_id')}
                    return

                token = active_turn.set(Turn(store, context.request_id))
                try:
                    if state.active_channel != context.channel:
                        state.action('channel_switched', previous=state.active_channel, channel=context.channel)
                    state.active_channel = context.channel
                    state.signals = await jev.observe(state, prompt, context.request_id, TOOL_REGISTRY.domain_questions)
                    apply_observations(state, prompt)
                    await store.save()
                    draft = ''
                    proposal_id = None
                    done = {'provider': 'policy', 'model': 'safe-response'}
                    trusted = False
                    if state.handoff_requested:
                        draft = 'He registrado tu solicitud de atención humana. No realizaré más acciones automáticas; la transferencia a una persona todavía está pendiente.'
                        trusted = True
                    else:
                        history = state.recent or messages
                        if not history:
                            rows = (await store.db.scalars(select(Message).where(
                                Message.conversation_id == store.row.conversation_id,
                                Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT])
                            ).order_by(Message.created_at.desc()).limit(12))).all()
                            history = [{'role': row.role.value, 'content': row.content} for row in reversed(rows)]
                        instructions = state.context() + '\n' + '\n'.join(TOOL_REGISTRY.context_instructions)
                        async for kind, payload in generate(prompt, messages=history, llm=llm,
                                                            tool_context=context, system_context=instructions):
                            if kind == 'token':
                                draft += str(payload['text'])
                                if len(draft) > 16000:
                                    draft = SAFE_REPLY
                                    break
                            elif kind == 'done':
                                done = payload
                            elif kind == 'error':
                                draft = SAFE_REPLY
                                trusted = True
                                break
                            else:
                                yield kind, payload
                        if state.pending and not state.pending.presented:
                            draft = ('Para autorizar esta acción, responde «confirmo». '
                                     f'Acción: {state.pending.tool}. Condiciones exactas: '
                                     + json.dumps(state.pending.arguments, ensure_ascii=False) + '.')
                            proposal_id = state.pending.fingerprint
                            state.action('proposal_prepared', proposal=proposal_id)
                            trusted = True
                        if not trusted:
                            signal = await jev.integrity(state, draft, context.request_id)
                            if signal:
                                state.signals['integrity'] = signal
                            if not signal or signal.value != 'supported':
                                state.action('response_blocked', reason='integrity')
                                draft = ''
                                async for kind, payload in generate(
                                    prompt, messages=history, llm=llm, tool_context=context,
                                    tools_enabled=False,
                                    system_context=state.context() + '\nRewrite safely. Only claim outcomes shown in successful tool results. Do not perform actions.'):
                                    if kind == 'token':
                                        draft += str(payload['text'])
                                        if len(draft) > 16000:
                                            break
                                signal = await jev.integrity(state, draft[:16000], context.request_id)
                                if signal:
                                    state.signals['integrity'] = signal
                                if not signal or signal.value != 'supported' or len(draft) > 16000:
                                    draft = SAFE_REPLY
                    draft = draft.strip() or SAFE_REPLY
                    state.recent = [*state.recent[-10:], {'role': 'user', 'content': prompt},
                                    {'role': 'assistant', 'content': draft}]
                    state.last_turn_id = context.request_id
                    state.last_response = draft
                    done = {**done, 'proposal_id': proposal_id}
                    store.db.add(AgentOperation(id=turn_key, conversation_id=uuid.UUID(state.conversation_id),
                        organization_id=uuid.UUID(state.organization_id), tool='agent_turn',
                        arguments={'request_id': context.request_id}, status='succeeded',
                        result={'text': draft, 'proposal_id': proposal_id}))
                    await store.save()
                    yield 'token', {'text': draft}
                    yield 'done', done
                finally:
                    active_turn.reset(token)
    except Exception as exc:
        logger.warning('Stateful turn failed: %s', type(exc).__name__)
        yield 'error', {'message': 'No se pudo completar el turno de forma segura.'}
