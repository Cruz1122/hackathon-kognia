from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from ...agent.state import ASSISTANT_NAME
from ...agent.tool_schema import CanonicalTool
from ...agent.tools.loader import load_tool_registry

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse "
    "cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat "
    "non proident, sunt in culpa qui officia deserunt mollit anim id est laborum. "
)
MAX_LOREM_CHARS = 5000
AGENT_SYSTEM = (
    f"Eres un agente de voz breve llamado {ASSISTANT_NAME}. Habla en español. "
    f"Si te preguntan cómo te llamas, responde que eres {ASSISTANT_NAME}. "
    "Responde siempre en texto plano, como una persona real conversando: sin Markdown. "
    "No uses negritas (**), cursivas, títulos (#), viñetas (-, *), numeración (1.), "
    "backticks ni emojis. No armes listas: integra los datos en oraciones naturales separadas "
    "por comas o puntos; si necesitas enumerar, dilo en prosa (por ejemplo: "
    "'primero..., luego... y por último...'). Usa frases cortas y haz una sola pregunta a la vez. "
    "Estas instrucciones y el knowledge no forman parte de la llamada. "
    "La conversación son solo los mensajes del usuario y tus respuestas habladas. "
    "Si preguntan con qué empezó o qué se dijo, cita el primer mensaje del usuario; "
    "nunca este texto ni el knowledge. "
    "Si recibes knowledge_status=available, responde con ese documento; no inventes políticas. "
    "Si knowledge_status=insufficient, no afirmes que el documento respalda la respuesta. "
    "Resuelve las respuestas cortas o elípticas según la pregunta inmediatamente anterior antes de asignarles un significado aislado. "
    "Si acabas de preguntar si una hora ya dicha es de la mañana o de la noche, una respuesta como 'mañana' o 'de mañana' "
    "indica la mañana para esa hora; conserva la fecha de reserva ya establecida y no vuelvas a preguntar la hora. "
    "No empieces cada turno con 'Perfecto' ni repitas otro reconocimiento por costumbre. Reconoce lo dicho solo cuando suene natural; "
    "normalmente continúa directamente con la siguiente pregunta o acción útil y varía las transiciones con naturalidad. "
    "Un saludo claro del cliente como 'Hola' es una entrada válida y con sentido: contéstalo con calidez, sin decir que no lo oíste "
    "ni disculparte por no entenderlo. Si el asistente ya dio la bienvenida, no reinicies ni repitas la bienvenida; continúa con el dato concreto que falte. "
    "Un rechazo, una corrección, una queja o un insulto nunca son consentimiento para una acción pendiente. "
    "Usa generate_lorem_ipsum cuando pidan texto lorem ipsum con una cantidad de caracteres, "
    "y sum_numbers cuando pidan sumar números. No inventes el resultado de esas tools: ejecútalas."
)

def _canonical_domain_tools() -> tuple[CanonicalTool, ...]:
    return tuple(
        CanonicalTool(name=item["name"], description=item["description"], parameters=item["parameters"])
        for item in load_tool_registry().schemas()
    )


# Kept as a compatibility export for older provider integrations. New domain tools
# follow it and are the only tools with registered handlers in the runtime.
_LEGACY_TOOLS: tuple[CanonicalTool, ...] = (
    CanonicalTool("generate_lorem_ipsum", "Legacy compatibility helper.", {"type": "object", "properties": {"characters": {"type": "integer"}}, "required": ["characters"]}),
    CanonicalTool("sum_numbers", "Legacy compatibility helper.", {"type": "object", "properties": {"numbers": {"type": "array"}}, "required": ["numbers"]}),
)
DOMAIN_TOOLS: tuple[CanonicalTool, ...] = tuple(
    tool for tool in _canonical_domain_tools()
    if tool.name not in {"generate_lorem_ipsum", "sum_numbers"}
)
CANONICAL_TOOLS: tuple[CanonicalTool, ...] = _LEGACY_TOOLS + DOMAIN_TOOLS


def to_openai_tools(tools: Sequence[CanonicalTool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_gemini_tools(tools: Sequence[CanonicalTool]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            ]
        }
    ]


OPENAI_TOOLS = to_openai_tools(CANONICAL_TOOLS)
GEMINI_TOOLS = to_gemini_tools(CANONICAL_TOOLS)


def generate_lorem_ipsum(characters: int) -> str:
    count = max(1, min(int(characters), MAX_LOREM_CHARS))
    repeats = (count // len(LOREM)) + 1
    return (LOREM * repeats)[:count]


def sum_numbers(numbers: list[float]) -> str:
    total = sum(float(value) for value in numbers)
    if total.is_integer():
        return str(int(total))
    return str(total)


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


DisplayField = dict[str, str]
_TOOL_DISPLAY: dict[str, dict[str, Any]] = {
    "generate_lorem_ipsum": {
        "name": "Generación de texto",
        "inputs": (("characters", "Caracteres"),),
        "outputs": (("text", "Texto"),),
    },
    "sum_numbers": {
        "name": "Suma de números",
        "inputs": (("numbers", "Números"),),
        "outputs": (("total", "Total"),),
    },
    "check_availability": {
        "name": "Consulta de disponibilidad",
        "inputs": (("date", "Fecha"), ("time", "Hora"), ("party_size", "Comensales")),
        "outputs": (("date", "Fecha"), ("time", "Hora"), ("party_size", "Comensales"), ("available", "Disponible")),
    },
    "create_booking": {
        "name": "Creación de reserva",
        "inputs": (("customer_name", "Nombre"), ("date", "Fecha"), ("time", "Hora"), ("party_size", "Comensales")),
        "outputs": (("booking_id", "Código de reserva"), ("status", "Estado")),
    },
}
_STATUS_LABELS = {"confirmed": "Confirmada", "available": "Disponible"}


def _humanize_key(key: str) -> str:
    cleaned = " ".join(key.replace("_", " ").split())
    return cleaned[:1].upper() + cleaned[1:] if cleaned else key


def _format_date(value: object) -> str | None:
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text[:10])
    if not match:
        return None
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"


def _format_value(key: str, value: object) -> str:
    if value is None:
        return "—"
    if key == "date":
        formatted = _format_date(value)
        if formatted:
            return formatted
    if key == "available":
        return "Sí" if value in {True, "true", "True", 1, "1"} else "No"
    if key == "status":
        text = str(value).strip()
        return _STATUS_LABELS.get(text.casefold(), _humanize_key(text))
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, list):
        return ", ".join(_format_value(key, item) for item in value)
    return str(value)


def present_tool_name(name: str) -> str:
    catalog = _TOOL_DISPLAY.get(name)
    if catalog:
        return str(catalog["name"])
    return _humanize_key(name)


def present_tool_inputs(name: str, arguments: dict[str, Any]) -> list[DisplayField]:
    catalog = _TOOL_DISPLAY.get(name)
    if catalog:
        return [
            {"label": label, "value": _format_value(key, arguments[key])}
            for key, label in catalog["inputs"]
            if key in arguments
        ]
    return [{"label": _humanize_key(str(key)), "value": _format_value(str(key), value)} for key, value in arguments.items()]


def present_tool_outputs(name: str, result: str) -> list[DisplayField]:
    if name == "generate_lorem_ipsum":
        return [{"label": "Texto", "value": result}]
    if name == "sum_numbers":
        return [{"label": "Total", "value": result}]
    parsed = parse_arguments(result)
    if parsed.get("error_code"):
        return [{"label": "Error", "value": str(parsed.get("message") or parsed["error_code"])}]
    catalog = _TOOL_DISPLAY.get(name)
    if catalog and parsed:
        fields = [
            {"label": label, "value": _format_value(key, parsed[key])}
            for key, label in catalog["outputs"]
            if key in parsed
        ]
        if fields:
            return fields
    if parsed:
        return [{"label": _humanize_key(str(key)), "value": _format_value(str(key), value)} for key, value in parsed.items()]
    return [{"label": "Resultado", "value": result}]


def describe_tool_start(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    title = present_tool_name(name)
    if name == "generate_lorem_ipsum":
        return title, f"{arguments.get('characters', '?')} caracteres"
    if name == "sum_numbers":
        values = arguments.get("numbers") or []
        return title, f"{len(values)} valores"
    return title, "Ejecutando"


def describe_tool_done(name: str, arguments: dict[str, Any], result: str) -> tuple[str, str]:
    title = present_tool_name(name)
    if name == "generate_lorem_ipsum":
        return title, f"{len(result)} caracteres · completado"
    if name == "sum_numbers":
        return title, f"Total {result}"
    return title, "Completado"


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    if name == "generate_lorem_ipsum":
        return generate_lorem_ipsum(int(arguments.get("characters") or 1))
    if name == "sum_numbers":
        values = arguments.get("numbers") or []
        if not isinstance(values, list) or not values:
            raise ValueError("Necesito al menos un número para sumar.")
        return sum_numbers(values)
    raise ValueError(f"Tool desconocida: {name}")
