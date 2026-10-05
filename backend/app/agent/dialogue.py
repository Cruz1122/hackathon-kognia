"""Small presentation and execution helpers shared by agent channels."""
from __future__ import annotations

import json
from datetime import date as Date, timedelta
from typing import Any, AsyncIterator

from .state import local_now
from .tools.contracts import ToolResult

def reservation_question(args):
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
    return f"Sería una mesa para {args['party_size']} {people} {when}, {clock}, a nombre de {args['customer_name']}. ¿La confirmas?"


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
