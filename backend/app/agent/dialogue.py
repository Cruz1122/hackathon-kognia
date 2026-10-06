"""Small presentation and execution helpers shared by agent channels."""
from __future__ import annotations

import json
import re
from datetime import date as Date, timedelta
from typing import Any, AsyncIterator

from .state import AgentState, Fact, local_now
from .tools.contracts import ToolResult

def _reservation_when(args: dict[str, Any]) -> tuple[str, str, str]:
    day = Date.fromisoformat(args['date'])
    today = local_now().date()
    months = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
              'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')
    when = 'mañana' if day == today + timedelta(days=1) else 'hoy' if day == today else f'el {day.day} de {months[day.month - 1]}'
    if day.year != today.year:
        when += f' de {day.year}'
    hour, minute = map(int, args['time'].split(':'))
    hours = ('doce', 'una', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve', 'diez', 'once')
    clock = f"a {'la' if hour % 12 == 1 else 'las'} {hours[hour % 12]}"
    if minute:
        clock = f"a las {args['time']}"
    else:
        clock += ' del mediodía' if hour == 12 else ' de la mañana' if 0 < hour < 12 else ' de la tarde' if 12 < hour < 19 else ' de la noche'
    people = 'persona' if args['party_size'] == 1 else 'personas'
    return when, clock, people


def reservation_question(args: dict[str, Any]) -> str:
    when, clock, people = _reservation_when(args)
    return f"Sería una mesa para {args['party_size']} {people} {when}, {clock}, a nombre de {args['customer_name']}. ¿La confirmas?"


def reservation_confirmation(args: dict[str, Any], result: Any = None) -> str:
    when, clock, people = _reservation_when(args)
    code = result.get('booking_id') if isinstance(result, dict) else None
    code_text = f' Tu código es {code}.' if code else ''
    return (
        f"Listo, {args['customer_name']}: tu reserva para {args['party_size']} {people} "
        f"{when}, {clock}, quedó confirmada.{code_text} Gracias por tu llamada. ¡Hasta luego!"
    )


_NAME_PATTERN = re.compile(
    r'\b(?:me\s+llamo|mi\s+nombre\s+es|soy)\s+'
    r'([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\'’-]*'
    r'(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\'’-]*){0,2}?)'
    r'(?=\s*(?:[,.;!?]|$|\by\b|\bpara\b))',
    re.IGNORECASE,
)


def customer_name(text: str) -> str | None:
    """Extract a name only from an explicit self-introduction."""
    match = _NAME_PATTERN.search(text or '')
    if not match:
        return None
    name = ' '.join(match.group(1).split()).strip()
    return name[:120] or None


def remember_customer_name(state: AgentState, text: str) -> str | None:
    name = customer_name(text)
    if not name:
        return None
    state.booking_slots['customer_name'] = name
    state.facts['customer.name'] = Fact(value=name, source='customer_statement')
    return name


def booking_arguments(state: AgentState) -> dict[str, Any] | None:
    required = ('date', 'time', 'party_size', 'customer_name')
    if not all(state.booking_slots.get(key) not in (None, '') for key in required):
        return None
    return {key: state.booking_slots[key] for key in required}


def next_question(state: AgentState) -> str | None:
    slots = state.booking_slots
    if not slots.get('customer_name'):
        return '¿A nombre de quién hago la reserva?'
    if not slots.get('date'):
        return '¿Para qué día quieres la reserva?'
    if not slots.get('time'):
        return '¿A qué hora quieres la reserva?'
    if not slots.get('party_size'):
        return '¿Para cuántas personas sería la reserva?'
    return None


def resume_message(state: AgentState) -> tuple[str, str | None]:
    """Return an immediate, state-backed greeting for a connected callback."""
    if state.pending and state.pending.tool == 'create_booking':
        return 'Hola. Retomemos donde quedamos. ' + reservation_question(state.pending.arguments), state.pending.fingerprint
    if state.phase == 'completed' and any(
        item.get('tool') == 'create_booking' and item.get('ok') for item in state.tool_history
    ):
        return 'Hola. Tu reserva ya está confirmada. ¿Necesitas algo más?', None
    question = next_question(state)
    if question:
        return 'Hola. Retomemos donde quedamos con tu reserva. ' + question, None
    return 'Hola. Retomemos exactamente donde se interrumpió la conversación.', None


def continuation_text(state: AgentState, *, apologize: bool) -> tuple[str, str | None]:
    prefix = (
        'Lamento que la llamada terminara antes de que pudiera responderte. '
        if apologize else 'Gracias por tu llamada. '
    )
    if state.pending and state.pending.tool == 'create_booking':
        return prefix + reservation_question(state.pending.arguments), state.pending.fingerprint
    question = next_question(state)
    if question:
        return prefix + 'Podemos continuar por aquí. ' + question, None
    return prefix + 'Podemos continuar por aquí con lo que quedó pendiente. ¿Quieres que retomemos?', None


async def execute(name, args, context) -> AsyncIterator[tuple[str, dict[str, Any] | ToolResult]]:
    from ..features.agent.service import TOOL_REGISTRY
    from ..features.agent.tools import describe_tool_start, describe_tool_done, present_tool_inputs, present_tool_outputs
    title, status = describe_tool_start(name, args)
    yield 'tool.started', {'tool': name, 'arguments': args, 'title': title, 'status': status,
                          'inputs': present_tool_inputs(name, args)}
    result = await TOOL_REGISTRY.execute(name, args, context)
    rendered = json.dumps(result.data if result.ok else {'error_code': result.error_code, 'message': result.message}, ensure_ascii=False)
    title, status = describe_tool_done(name, args, rendered)
    yield 'tool.completed', {'tool': name, 'ok': result.ok, 'result': rendered, 'error_code': result.error_code,
                            'title': title, 'status': status if result.ok else 'Pendiente de revisión',
                            'outputs': present_tool_outputs(name, rendered)}
    yield '_result', result
