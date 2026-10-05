"""One turn coordinator shared by every channel; no unvalidated tokens escape."""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from . import dialogue, jev
from .policy import Turn, active_turn, apply_observations
from .state import Fact, Signal, fingerprint
from .phrases import RECOVERY, pick
from .store import conversation_state
from .tools.contracts import ToolResult

logger = logging.getLogger(__name__)


async def stateful_stream(prompt, *, messages, llm, tool_context, generate):
    context = tool_context
    response_emitted = False
    try:
        async with asyncio.timeout(120):
            async with conversation_state(context.organization_id, context.conversation_id) as store:
                state = store.state
                from ..db.models import AgentOperation, Message, MessageRole
                from ..features.agent.service import TOOL_REGISTRY

                from ..whatsapp.identity import verified_destination
                binding = await verified_destination(store.db, store.row.organization_id, store.row.conversation_id)
                if binding:
                    state.facts['customer.phone'] = Fact(value=binding.phone, source='verified_channel_binding')
                else:
                    state.facts.pop('customer.phone', None)
                turn_key = fingerprint(state.conversation_id, {'turn': context.request_id})
                previous = await store.db.get(AgentOperation, turn_key)
                if previous and previous.status == 'succeeded' and previous.result:
                    response_emitted = True
                    yield 'token', {'text': previous.result['text']}
                    yield 'done', {'provider': 'state', 'model': 'replay', 'proposal_id': previous.result.get('proposal_id')}
                    return

                token = active_turn.set(Turn(store, context.request_id))
                try:
                    rows = (await store.db.scalars(select(Message).where(
                        Message.conversation_id == store.row.conversation_id,
                        Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT])
                    ).order_by(Message.created_at.desc(), Message.id.desc()).limit(24))).all()
                    history = [{'role': row.role.value, 'content': row.content} for row in reversed(rows)]
                    # Some transports persist the incoming message before the turn.
                    if history and history[-1] == {'role': 'user', 'content': prompt}:
                        history.pop()
                    state.recent = history or state.recent or messages or []
                    if state.active_channel != context.channel:
                        state.action('channel_switched', previous=state.active_channel, channel=context.channel)
                    state.active_channel = context.channel
                    signals = await jev.observe(state, prompt, context.request_id, TOOL_REGISTRY.domain_questions)
                    state.signals = signals
                    callback_intent = signals.get('callback_request')
                    if (callback_intent and callback_intent.value == 'explicit' and callback_intent.confidence >= .8
                            and callback_intent.turn_id == context.request_id):
                        state.signals['callback_request'] = Signal(
                            value='explicit', confidence=callback_intent.confidence,
                            turn_id=context.request_id, model=callback_intent.model)
                    confirmation = state.signals.get('confirmation')
                    if state.pending and state.pending.presented and (confirmation is None or confirmation.value == 'uncertain'):
                        fallback = await jev.confirmation_fallback(state, prompt, context.request_id, llm)
                        if fallback is not None:
                            state.signals['confirmation'] = fallback
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
                        history = state.recent
                        execution = None
                        if state.authorized and state.pending:
                            pending = state.pending
                            async for kind, value in dialogue.execute(pending.tool, pending.arguments, context):
                                if kind == '_result':
                                    execution = (pending.tool, value)
                                else:
                                    yield kind, value
                        instructions = state.context() + '\n' + '\n'.join(TOOL_REGISTRY.context_instructions)
                        write_attempted = execution is not None
                        if execution is not None:
                            name, result = execution
                            if not isinstance(result, ToolResult):
                                raise TypeError('Tool execution returned an invalid result')
                            if result.ok:
                                draft = (('Tu reserva está confirmada. Gracias por llamar. ¡Hasta luego!'
                                          if context.channel == 'voice' else 'Tu reserva está confirmada.')
                                         if name == 'create_booking' else
                                         'Te estoy llamando para retomar lo pendiente. Contesta cuando suene.')
                            else:
                                draft = 'La solicitud está pendiente de confirmación. No voy a repetirla para evitar duplicados.'
                            trusted = True
                        else:
                            async for kind, payload in generate(
                                prompt, messages=history, llm=llm, tool_context=context, system_context=instructions):
                                if kind == 'token':
                                    draft += str(payload['text'])
                                    if len(draft) > 16000:
                                        draft = pick(RECOVERY, state.last_response)
                                        break
                                elif kind == 'done':
                                    done = payload
                                elif kind == 'error':
                                    draft = pick(RECOVERY, state.last_response)
                                    trusted = True
                                    break
                                else:
                                    if kind == 'tool.completed':
                                        definition = TOOL_REGISTRY.resolve(payload.get('tool', ''))
                                        if definition and definition.side_effects == 'write':
                                            error = payload.get('error_code')
                                            if error is None and isinstance(payload.get('result'), str):
                                                import json
                                                try:
                                                    error = json.loads(payload['result']).get('error_code')
                                                except (ValueError, AttributeError):
                                                    pass
                                            if error != 'CONFIRMATION_REQUIRED':
                                                write_attempted = True
                                    yield kind, payload
                        if state.pending and not write_attempted:
                            if not state.pending.presented:
                                proposal_id = state.pending.fingerprint
                                state.action('proposal_prepared', proposal=proposal_id)
                            # A confirmation must actually present the bound action,
                            # not a model's promise to execute it without consent.
                            if state.pending.tool == 'create_booking':
                                args = state.pending.arguments
                                draft = dialogue.reservation_question(args)
                                trusted = True
                            elif state.pending.tool == 'call_customer':
                                draft = '¿Confirmas que te llame al teléfono de esta conversación?'
                                trusted = True
                        # Integrity must see the customer's actual words, including
                        # names/details not yet promoted to tool-backed facts.
                        state.recent = [*history, {'role': 'user', 'content': prompt}]
                        signal = await jev.integrity(state, draft, context.request_id)
                        if signal:
                            state.signals['integrity'] = signal
                        if not trusted:
                            # Only an explicit "unsupported" blocks; a missing verdict does not.
                            if signal is not None and signal.value == 'unsupported':
                                state.action('response_blocked', reason='integrity')
                                draft = ''
                                async for kind, payload in generate(
                                    prompt, messages=history, llm=llm, tool_context=context,
                                    tools_enabled=False,
                                    system_context=state.context() + '\n' + '\n'.join(TOOL_REGISTRY.context_instructions)
                                    + '\nRewrite safely and naturally. Preserve the known task and details from the conversation. '
                                    'Use the supplied domain constraints and evidence; do not replace them with a generic inability to help. '
                                    'If a transcribed number is ambiguous, ask about that number. '
                                    'Only claim outcomes shown in successful tool results. Do not perform actions.'):
                                    if kind == 'token':
                                        draft += str(payload['text'])
                                        if len(draft) > 16000:
                                            break
                                signal = await jev.integrity(state, draft[:16000], context.request_id)
                                if signal:
                                    state.signals['integrity'] = signal
                                if (signal is not None and signal.value == 'unsupported') or len(draft) > 16000:
                                    draft = pick(RECOVERY, state.last_response)
                    draft = draft.strip() or pick(RECOVERY, state.last_response)
                    state.recent = [*history[-22:], {'role': 'user', 'content': prompt},
                                    {'role': 'assistant', 'content': draft}]
                    state.last_turn_id = context.request_id
                    state.last_response = draft
                    done = {**done, 'proposal_id': proposal_id}
                    store.db.add(AgentOperation(id=turn_key, conversation_id=uuid.UUID(state.conversation_id),
                        organization_id=uuid.UUID(state.organization_id), tool='agent_turn',
                        arguments={'request_id': context.request_id}, status='succeeded',
                        result={'text': draft, 'proposal_id': proposal_id}))
                    await store.save()
                    response_emitted = True
                    yield 'agent.signals', {
                        'signals': {
                            key: signal.model_dump(mode='json')
                            for key, signal in state.signals.items()
                        }
                    }
                    yield 'token', {'text': draft}
                    yield 'done', done
                finally:
                    active_turn.reset(token)
    except Exception as exc:
        logger.warning('Stateful turn failed: %s', type(exc).__name__)
        if response_emitted:
            return
        # Keep the conversation natural instead of exposing an internal error.
        yield 'token', {'text': pick(RECOVERY)}
        yield 'done', {'provider': 'fallback', 'model': 'hold', 'proposal_id': None}
