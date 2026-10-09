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
    "Para información sobre IPS usa las tools oficiales. Si la consulta trata de capacidad, aclara que la capacidad instalada registrada no es disponibilidad actual. "
    "Nunca inventes sedes, servicios, horarios, teléfonos, citas, camas disponibles ni información clínica. "
    "Resuelve las respuestas cortas o elípticas según la pregunta inmediatamente anterior antes de asignarles un significado aislado. "
    "No empieces cada turno con 'Perfecto' ni repitas otro reconocimiento por costumbre. Reconoce lo dicho solo cuando suene natural; "
    "normalmente continúa directamente con la siguiente pregunta o acción útil y varía las transiciones con naturalidad. "
    "Un saludo claro del cliente como 'Hola' es una entrada válida y con sentido: contéstalo con calidez, sin decir que no lo oíste "
    "ni disculparte por no entenderlo. Si el asistente ya dio la bienvenida, no reinicies ni repitas la bienvenida; continúa con el dato concreto que falte. "
    "Un rechazo, una corrección, una queja o un insulto nunca son consentimiento para una acción pendiente. "
    "Si falta la ciudad, el departamento o el nombre de la sede, pregunta solo eso. "
    "Cuando ya hay una búsqueda, una pregunta como cuál tiene más camas se refiere a esas sedes. "
    "Si el usuario nombra otro lugar, olvida el lugar y los resultados anteriores. "
    "Cuando pregunten por una IPS, llama la tool con lo que dijeron: nombre en query, ciudad en municipality, "
    "departamento en department, hospital o clínica en kind, pública o privada en nature y la capacidad en capacity. "
    "Responde en español hablado, en oraciones corridas, solo con lo que devolvió la tool. "
    "Si hay varias, menciona las primeras y pregunta si quieren precisar. Si no hay resultados, dilo y pide un dato. "
    "No narres la herramienta ni inventes sedes, teléfonos, direcciones o cantidades."
)

def _canonical_domain_tools() -> tuple[CanonicalTool, ...]:
    return tuple(
        CanonicalTool(name=item["name"], description=item["description"], parameters=item["parameters"])
        for item in load_tool_registry().schemas()
    )


CANONICAL_TOOLS: tuple[CanonicalTool, ...] = _canonical_domain_tools()


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


def _structured_json(value: object) -> str | None:
    structured = isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value)
    )
    if not structured:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return None


def _format_value(key: str, value: object) -> str:
    if value is None:
        return "—"
    structured = _structured_json(value)
    if structured is not None:
        return structured
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
