"""One turn coordinator shared by every channel; no unvalidated tokens escape."""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from . import jev
from .policy import Turn, active_turn, apply_observations, mentions_emergency
from .state import Fact, Signal, fingerprint
from .phrases import IPS_GREETING, RECOVERY, pick
from .store import conversation_state
from .tools.contracts import ToolResult

logger = logging.getLogger(__name__)


async def stateful_stream(prompt, *, messages, llm, tool_context, generate):
    del messages  # Caller transcripts are not this conversation's memory.
    context = tool_context
    response_emitted = False
    try:
        async with asyncio.timeout(120):
            async with conversation_state(context.organization_id, context.conversation_id) as store:
                state = store.state
                from ..db.models import AgentOperation, Customer, Message, MessageRole
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
                    ).order_by(Message.created_at.asc(), Message.id.asc()))).all()
                    history = [{'role': row.role.value, 'content': row.content} for row in rows]
                    # Some transports persist the incoming message before the turn.
                    if history and history[-1] == {'role': 'user', 'content': prompt}:
                        history.pop()
                    # Memory is this conversation's transcript. An empty thread must
                    # not inherit client messages or a snapshot left by another call.
                    state.recent = history
                    customer_row = None
                    if state.customer_id:
                        customer_row = await store.db.get(Customer, uuid.UUID(state.customer_id))
                        if customer_row is not None and customer_row.name:
                            state.facts.setdefault('customer.name', Fact(value=customer_row.name, source='customer_record'))
                    if state.active_channel != context.channel:
                        state.action('channel_switched', previous=state.active_channel, channel=context.channel)
                    state.active_channel = context.channel
                    signals = await jev.observe(state, prompt, context.request_id, TOOL_REGISTRY.domain_questions)
                    state.signals = signals
                    if context.system_initiated:
                        # An internal prompt is not a user utterance, so it cannot
                        # carry an intent or an emergency.
                        state.signals.pop('intent', None)
                    elif mentions_emergency(prompt):
                        state.signals['intent'] = Signal(
                            value='emergencia', confidence=1, turn_id=context.request_id, model='keyword-guard',
                            probabilities={'emergencia': 1})
                    apply_observations(state, prompt)
                    intent_signal = state.signals.get('intent')
                    from ..domains.ips.memory import choose_stage, remember_tool, update_memory
                    if context.system_initiated:
                        state.stage = 'inicio' if not history else state.stage
                    else:
                        update_memory(state.ips, prompt, intent_signal.value if intent_signal else None)
                        state.stage = choose_stage(state.ips, prompt, first_turn=not history)
                    state.phase = 'understanding'
                    logger.info(
                        "Agent turn request_id=%s channel=%s system_initiated=%s intent=%s authorized=%s",
                        context.request_id, context.channel, context.system_initiated,
                        intent_signal.value if intent_signal else None,
                        state.authorized)
                    await store.save()
                    # Voice can decide whether a backchannel is safe without
                    # inspecting user text.  The transport receives the
                    # already-evaluated state/guard before model generation.
                    yield 'agent.guard', {
                        'allow_backchannel': state.stage != 'emergencia',
                        'stage': state.stage,
                        'handoff_requested': state.handoff_requested,
                    }
                    draft = ''
                    proposal_id = None
                    done = {'provider': 'policy', 'model': 'safe-response'}
                    trusted = False
                    if state.stage == 'inicio' and not context.system_initiated:
                        draft = IPS_GREETING
                        trusted = True
                    elif state.handoff_requested:
                        draft = 'He registrado tu solicitud de atención humana. No realizaré más acciones automáticas; la transferencia a una persona todavía está pendiente.'
                        trusted = True
                    else:
                        history = state.recent
                        # The generator records the retrieved knowledge it used so
                        # integrity can verify the draft against the same evidence.
                        knowledge_sink: list[str] = []
                        execution = None
                        instructions = state.context() + '\n' + '\n'.join(TOOL_REGISTRY.context_instructions)
                        if execution is not None:
                            raise RuntimeError('Removed side-effecting tool path')
                        else:
                            generated = generate(
                                prompt, messages=history, llm=llm, tool_context=context,
                                system_context=instructions, knowledge_sink=knowledge_sink,
                                tools_enabled=state.stage == 'buscando',
                                use_document_rag=False, history_limit=8)
                            try:
                                async for kind, payload in generated:
                                    stop_after_tool = False
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
                                            import json
                                            tool_name = payload.get('tool', '')
                                            error = payload.get('error_code')
                                            result_data = None
                                            if isinstance(payload.get('result'), str):
                                                try:
                                                    result_data = json.loads(payload['result'])
                                                    if error is None and isinstance(result_data, dict):
                                                        error = result_data.get('error_code')
                                                except (ValueError, AttributeError):
                                                    pass
                                            definition = TOOL_REGISTRY.resolve(tool_name)
                                            if (payload.get('ok') is True and isinstance(result_data, dict)
                                                    and result_data.get('status') == 'ok'):
                                                remember_tool(
                                                    state.ips,
                                                    payload.get('arguments') if isinstance(payload.get('arguments'), dict) else {},
                                                    result_data,
                                                )
                                        yield kind, payload
                                        if stop_after_tool:
                                            break
                            finally:
                                close = getattr(generated, 'aclose', None)
                                if close is not None:
                                    await close()
                        # Integrity must see the customer's actual words, including
                        # names/details not yet promoted to tool-backed facts.
                        state.recent = [*history, {'role': 'user', 'content': prompt}]
                        signal = await jev.integrity(state, draft, context.request_id, knowledge=knowledge_sink)
                        if signal:
                            state.signals['integrity'] = signal
                        if not trusted:
                            # Only an explicit "unsupported" blocks; a missing verdict does not.
                            if signal is not None and signal.value == 'unsupported':
                                state.action('response_blocked', reason='integrity')
                                draft = ''
                                async for kind, payload in generate(
                                    prompt, messages=history, llm=llm, tool_context=context,
                                    tools_enabled=False, knowledge_sink=knowledge_sink,
                                    use_document_rag=False, history_limit=8,
                                    system_context=state.context() + '\n' + '\n'.join(TOOL_REGISTRY.context_instructions)
                                    + '\nRewrite safely and naturally. Preserve the known task and details from the conversation. '
                                    'Use the supplied domain constraints and evidence; do not replace them with a generic inability to help. '
                                    'If a transcribed number is ambiguous, ask about that number. '
                                    'Only claim outcomes shown in successful tool results. Do not perform actions.'):
                                    if kind == 'token':
                                        draft += str(payload['text'])
                                        if len(draft) > 16000:
                                            break
                                signal = await jev.integrity(state, draft[:16000], context.request_id, knowledge=knowledge_sink)
                                if signal:
                                    state.signals['integrity'] = signal
                                if (signal is not None and signal.value == 'unsupported') or len(draft) > 16000:
                                    draft = pick(RECOVERY, state.last_response)
                    if state.stage == 'buscando':
                        state.stage = 'respondiendo'
                    state.phase = 'understanding'
                    draft = draft.strip() or pick(RECOVERY, state.last_response)
                    state.recent = [*history, {'role': 'user', 'content': prompt},
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
                        },
                        'behavior': state.agent_behavior(),
                        'state': {
                            'phase': state.phase,
                            'stage': state.stage,
                            'ips': state.ips.model_dump(mode='json'),
                        },
                    }
                    yield 'token', {'text': draft}
                    yield 'done', done
                finally:
                    try:
                        active_turn.reset(token)
                    except (ValueError, RuntimeError):
                        # FastAPI probes an SSE iterator in the request context,
                        # then StreamingResponse finishes it in its own copied
                        # context. A ContextVar token cannot cross that boundary;
                        # clearing the copied context avoids both leakage and a
                        # false post-response failure after the turn committed.
                        active_turn.set(None)
    except Exception as exc:
        logger.warning('Stateful turn failed: %s', type(exc).__name__, exc_info=True)
        if response_emitted:
            return
        # Keep the conversation natural instead of exposing an internal error.
        yield 'token', {'text': pick(RECOVERY)}
        yield 'done', {'provider': 'fallback', 'model': 'hold', 'proposal_id': None}
