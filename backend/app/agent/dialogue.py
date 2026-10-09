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


_NAME_WORD = r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’-]*"
_NAME_PATTERN = re.compile(
    rf'\b(?:me\s+llamo|mi\s+nombre\s+es|soy|a\s+nombre\s+de)\s+'
    rf'({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}}?)'
    r'(?=\s*(?:[,.;!?]|$|\by\b|\bpara\b))',
    re.IGNORECASE,
)
_NAME_QUESTION = re.compile(
    r'a nombre de qui[eé]n|c[oó]mo te llamas|cu[aá]l es tu nombre|tu nombre',
    re.IGNORECASE,
)
_NOT_A_NAME = frozenset({
    'hola', 'buenas', 'buenos', 'dias', 'días', 'tardes', 'noches', 'si', 'sí', 'no',
    'ok', 'vale', 'bueno', 'gracias', 'por', 'favor', 'reserva', 'reservar', 'quiero',
    'una', 'un', 'mesa', 'personas', 'persona', 'mañana', 'hoy', 'para', 'dos', 'tres',
    'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve', 'diez', 'once', 'doce',
})


def customer_name(text: str) -> str | None:
    """Extract a name only from an explicit self-introduction."""
    match = _NAME_PATTERN.search(text or '')
    if not match:
        return None
    name = ' '.join(match.group(1).split()).strip()
    return name[:120] or None


def _asked_for_name(previous: str) -> bool:
    return bool(_NAME_QUESTION.search(previous or ''))


def answered_name(text: str, previous_assistant: str = '') -> str | None:
    """A direct reply to the name question counts, not only 'me llamo'."""
    explicit = customer_name(text)
    if explicit:
        return explicit
    if not _asked_for_name(previous_assistant):
        return None
    cleaned = re.sub(r'[¡!¿?.,;:]+', ' ', text or '')
    cleaned = re.sub(r'\b(?:a nombre de|me llamo|mi nombre es|soy|por favor)\b', ' ', cleaned, flags=re.IGNORECASE)
    words = []
    for word in cleaned.split():
        token = word.casefold()
        if token in _NOT_A_NAME or word.isdigit() or not re.fullmatch(_NAME_WORD, word):
            break
        words.append(word)
        if len(words) == 3:
            break
    if not words:
        return None
    return ' '.join(words)[:120]


def remember_customer_name(state: AgentState, text: str, previous_assistant: str = '') -> str | None:
    name = answered_name(text, previous_assistant)
    if not name:
        return None
    state.booking_slots['customer_name'] = name
    state.facts['customer.name'] = Fact(value=name, source='customer_statement')
    return name


_MONTHS = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}
_COUNTS = {
    'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
    'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10, 'once': 11, 'doce': 12,
    'trece': 13, 'catorce': 14, 'quince': 15, 'dieciseis': 16, 'dieciséis': 16,
    'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19, 'veinte': 20,
}
_HOUR_WORDS = {
    'doce': 12, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
    'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10, 'once': 11,
}
_COUNT_PATTERN = '|'.join(sorted(_COUNTS, key=len, reverse=True))
_HOUR_PATTERN = '|'.join(sorted(_HOUR_WORDS, key=len, reverse=True))
_PARTY_PATTERN = re.compile(rf'\b(?:para\s+)?(\d{{1,2}}|{_COUNT_PATTERN})\s+personas?\b', re.IGNORECASE)
_ABSOLUTE_DATE = re.compile(
    r'\bel\s+(\d{1,2})\s+de\s+(' + '|'.join(_MONTHS) + r')(?:\s+de\s+(\d{4}))?\b',
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(
    rf'\ba\s+las?\s+(\d{{1,2}}|{_HOUR_PATTERN})(?::(\d{{2}}))?(?:\s+de\s+la\s+(mañana|tarde|noche))?',
    re.IGNORECASE,
)


def party_size(text: str) -> int | None:
    """Party size only when the customer said how many people."""
    matches = list(_PARTY_PATTERN.finditer(text or ''))
    if not matches:
        return None
    token = matches[-1].group(1).casefold()
    if token.isdigit():
        size = int(token)
        return size if 1 <= size <= 20 else None
    return _COUNTS.get(token)


def spoken_date(text: str) -> str | None:
    """Calendar day only from an explicit date in the customer's words."""
    folded = ' '.join((text or '').casefold().split())
    absolute = _ABSOLUTE_DATE.search(folded)
    if absolute:
        year = int(absolute.group(3) or local_now().year)
        try:
            return Date(year, _MONTHS[absolute.group(2).casefold()], int(absolute.group(1))).isoformat()
        except ValueError:
            return None
    without_time_of_day = re.sub(r'\b(?:de|por)\s+la\s+mañana\b', ' ', folded)
    today = local_now().date()
    if re.search(r'\bpasado\s+mañana\b', without_time_of_day):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r'\bmañana\b', without_time_of_day):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r'\bhoy\b', without_time_of_day):
        return today.isoformat()
    return None


def _clock_hour(hour: int, period: str | None) -> int | None:
    if period == 'mañana':
        if hour == 12:
            return 12
        return hour if 1 <= hour <= 11 else None
    if period == 'tarde':
        if hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 11 else None
    if period == 'noche':
        if hour == 12:
            return 0
        return hour + 12 if 1 <= hour <= 11 else None
    if 0 <= hour <= 23 and hour >= 12:
        return hour
    # A bare hour in a reservation is the evening sitting: "a las ocho" is 20:00.
    if 1 <= hour <= 11:
        return hour + 12
    return None


def spoken_time(text: str) -> str | None:
    """Clock time only from an explicit hour in the customer's words."""
    matches = list(_TIME_PATTERN.finditer(text or ''))
    if not matches:
        return None
    match = matches[-1]
    token = match.group(1).casefold()
    hour = int(token) if token.isdigit() else _HOUR_WORDS.get(token)
    if hour is None:
        return None
    minute = int(match.group(2) or 0)
    if minute > 59:
        return None
    clock = _clock_hour(hour, match.group(3).casefold() if match.group(3) else None)
    if clock is None:
        return None
    return f'{clock:02d}:{minute:02d}'


def remember_spoken_booking(state: AgentState, text: str, history: list[dict[str, Any]] | None = None) -> None:
    """Fill booking slots only from what the customer said in this conversation."""
    sequence = [
        {'role': item.get('role'), 'content': str(item.get('content') or '')}
        for item in history or []
        if item.get('role') in {'user', 'assistant'} and item.get('content')
    ]
    if text:
        sequence.append({'role': 'user', 'content': text})
    previous_assistant = ''
    for item in sequence:
        if item['role'] == 'assistant':
            previous_assistant = item['content']
            continue
        utterance = item['content']
        remember_customer_name(state, utterance, previous_assistant)
        size = party_size(utterance)
        if size is not None:
            state.booking_slots['party_size'] = size
        day = spoken_date(utterance)
        if day:
            state.booking_slots['date'] = day
        clock = spoken_time(utterance)
        if clock:
            state.booking_slots['time'] = clock


def booking_arguments(state: AgentState) -> dict[str, Any] | None:
    required = ('date', 'time', 'party_size', 'customer_name')
    if not all(state.booking_slots.get(key) not in (None, '') for key in required):
        return None
    return {key: state.booking_slots[key] for key in required}


def stated_booking(state: AgentState, arguments: dict[str, Any], *, require_name: bool) -> dict[str, Any] | None:
    """Return the spoken reservation when the tool arguments repeat it exactly."""
    spoken = booking_arguments(state)
    if spoken is None:
        return None
    keys = ('date', 'time', 'party_size', 'customer_name') if require_name else ('date', 'time', 'party_size')
    if any(key not in arguments or arguments.get(key) != spoken[key] for key in keys):
        return None
    return spoken


def pending_matches_slots(state: AgentState) -> bool:
    """A proposal may be read aloud only when every term was said in this conversation."""
    pending = state.pending
    spoken = booking_arguments(state)
    if pending is None or pending.tool != 'create_booking' or spoken is None:
        return False
    return all(pending.arguments.get(key) == spoken[key] for key in spoken)


def fresh_question(state: AgentState, history: list[dict[str, Any]] | None = None) -> str | None:
    """The next missing detail, unless that exact question was just asked."""
    question = next_question(state)
    if not question:
        return None
    previous = next(
        (str(item.get('content') or '') for item in reversed(history or []) if item.get('role') == 'assistant'),
        '',
    )
    if previous and question.casefold() in previous.casefold():
        return None
    return question


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
    if pending_matches_slots(state) and state.pending is not None:
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
    if pending_matches_slots(state) and state.pending is not None:
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
