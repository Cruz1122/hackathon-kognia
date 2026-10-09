"""One turn coordinator shared by every channel; no unvalidated tokens escape."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from . import dialogue, jev
from .policy import Turn, active_turn, apply_observations, authorize, claims_callback
from .state import Fact, Signal, fingerprint, now
from .phrases import RECOVERY, pick
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
                            state.booking_slots.setdefault('customer_name', customer_row.name)
                            state.facts.setdefault(
                                'customer.name', Fact(value=customer_row.name, source='customer_record')
                            )
                    dialogue.remember_spoken_booking(
                        state, '' if context.system_initiated else prompt, history,
                    )
                    spoken_name = dialogue.customer_name(prompt) if not context.system_initiated else None
                    if (state.pending and state.pending.tool == 'create_booking'
                            and not dialogue.pending_matches_slots(state)):
                        state.action('proposal_withdrawn', proposal=state.pending.fingerprint,
                                     reason='unstated_details')
                        state.pending = None
                        state.authorized = None
                    if spoken_name and customer_row is not None and customer_row.name != spoken_name:
                        customer_row.name = spoken_name
                    if state.active_channel != context.channel:
                        state.action('channel_switched', previous=state.active_channel, channel=context.channel)
                    state.active_channel = context.channel
                    signals = await jev.observe(state, prompt, context.request_id, TOOL_REGISTRY.domain_questions)
                    state.signals = signals
                    confirmation = state.signals.get('confirmation')
                    if state.pending and state.pending.presented and (confirmation is None or confirmation.value == 'uncertain'):
                        fallback = await jev.confirmation_fallback(state, prompt, context.request_id, llm)
                        if fallback is not None:
                            state.signals['confirmation'] = fallback
                    if context.system_initiated:
                        # An internal prompt (for example the outbound-call
                        # reconnect greeting) is not a customer utterance. JEV can
                        # still label it a callback from the surrounding history,
                        # which would place another call and loop.
                        state.signals.pop('callback_request', None)
                    apply_observations(state, prompt)
                    if (state.pending and state.pending.presented and state.authorized is None
                            and now() - state.pending.created_at >= timedelta(minutes=15)):
                        # Consent is deliberately short-lived. If the exact same
                        # proposal is resumed later, re-present it and start a
                        # fresh confirmation window instead of trapping the
                        # customer in an expired proposal that can never be
                        # authorized again.
                        state.pending.created_at = now()
                        state.pending.presented = False
                        state.action('proposal_reissued_after_expiry', proposal=state.pending.fingerprint)
                    callback_signal = state.signals.get('callback_request')
                    logger.info(
                        "Agent turn request_id=%s channel=%s system_initiated=%s callback_request=%s explicit_prob=%s authorized=%s",
                        context.request_id, context.channel, context.system_initiated,
                        callback_signal.value if callback_signal else None,
                        (callback_signal.probabilities or {}).get('explicit') if callback_signal else None,
                        state.authorized)
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
                        # The generator records the retrieved knowledge it used so
                        # integrity can verify the draft against the same evidence.
                        knowledge_sink: list[str] = []
                        execution = None
                        callback_completed = False
                        callback_authorized = state.callback_authorized_turn_id == context.request_id
                        if callback_authorized:
                            logger.info("Deterministic callback execution request_id=%s", context.request_id)
                            async for kind, value in dialogue.execute('call_customer', {}, context):
                                if kind == '_result':
                                    execution = ('call_customer', value)
                                else:
                                    yield kind, value
                        elif state.authorized and state.pending:
                            pending = state.pending
                            logger.info("Deterministic execution request_id=%s tool=%s", context.request_id, pending.tool)
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
                            logger.info("Deterministic result request_id=%s tool=%s ok=%s error=%s",
                                        context.request_id, name, result.ok, result.error_code)
                            if name == 'call_customer' and result.ok:
                                callback_completed = True
                            if result.ok:
                                if name == 'create_booking':
                                    booking_args = state.booking_slots or pending.arguments
                                    draft = dialogue.reservation_confirmation(booking_args, result.data)
                                elif isinstance(result.data, dict) and result.data.get('status') == 'already_active':
                                    draft = 'Ya tienes una llamada en curso con nosotros.'
                                else:
                                    draft = 'Te estoy llamando para retomar lo pendiente. Contesta cuando suene.'
                            else:
                                draft = 'La solicitud está pendiente de confirmación. No voy a repetirla para evitar duplicados.'
                            trusted = True
                        elif state.pending and state.pending.tool == 'create_booking' and dialogue.pending_matches_slots(state):
                            # A transported/reconnected conversation can already
                            # have a complete, presented booking proposal. Keep
                            # that proposal deterministic instead of asking the
                            # model to rediscover it (and potentially running a
                            # duplicate availability lookup) after a greeting or
                            # other non-confirming utterance.
                            draft = dialogue.reservation_question(state.pending.arguments)
                            trusted = True
                        else:
                            generated = generate(
                                prompt, messages=history, llm=llm, tool_context=context,
                                system_context=instructions, knowledge_sink=knowledge_sink)
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
                                            if error == 'DETAILS_NOT_STATED':
                                                question = dialogue.fresh_question(state, history)
                                                if question:
                                                    draft = question
                                                    trusted = True
                                                    stop_after_tool = True
                                            if definition and definition.side_effects == 'write':
                                                if error not in {'CONFIRMATION_REQUIRED', 'DETAILS_NOT_STATED'}:
                                                    write_attempted = True
                                            if tool_name == 'call_customer':
                                                logger.info("Model call_customer completed request_id=%s error=%s",
                                                            context.request_id, error)
                                                if error is None:
                                                    callback_completed = True
                                            if (tool_name == 'check_availability' and payload.get('ok') is True
                                                    and isinstance(result_data, dict) and result_data.get('available') is True):
                                                # Do not wait for another slow model round after the read tool.
                                                # The proposal uses only details the customer said in this
                                                # conversation, never party size or a schedule supplied by the model.
                                                booking_args = dialogue.booking_arguments(state)
                                                if booking_args is not None:
                                                    authorize(state, 'create_booking', booking_args)
                                                    stop_after_tool = True
                                                else:
                                                    question = dialogue.fresh_question(state, history)
                                                    if question:
                                                        draft = question
                                                        trusted = True
                                                        stop_after_tool = True
                                                await store.save()
                                        yield kind, payload
                                        if stop_after_tool:
                                            break
                            finally:
                                close = getattr(generated, 'aclose', None)
                                if close is not None:
                                    await close()
                        if state.pending and not write_attempted:
                            if not state.pending.presented:
                                proposal_id = state.pending.fingerprint
                                state.action('proposal_prepared', proposal=proposal_id)
                            # A confirmation must actually present the bound action,
                            # not a model's promise to execute it without consent.
                            if state.pending.tool == 'create_booking' and dialogue.pending_matches_slots(state):
                                args = state.pending.arguments
                                draft = dialogue.reservation_question(args)
                                trusted = True
                            elif state.pending.tool == 'create_booking':
                                state.action('proposal_withdrawn', proposal=state.pending.fingerprint,
                                             reason='unstated_details')
                                state.pending = None
                                state.authorized = None
                                question = dialogue.fresh_question(state, history)
                                if question:
                                    draft = question
                                    trusted = True
                            elif state.pending.tool == 'call_customer':
                                draft = '¿Confirmas que te llame al teléfono de esta conversación?'
                                trusted = True
                        if claims_callback(draft) and not callback_completed:
                            # Never let the model claim a call it did not place this turn.
                            logger.warning("Blocked callback claim without a completed call request_id=%s draft=%r",
                                           context.request_id, draft[:120])
                            state.action('callback_claim_blocked', reason='no_call_this_turn')
                            draft = 'Aún no he podido iniciar la llamada. ¿Quieres que lo intente ahora?'
                            trusted = True
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
                        'state': {
                            'phase': state.phase,
                            'booking_slots': state.booking_slots,
                            'customer_name': state.booking_slots.get('customer_name'),
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
