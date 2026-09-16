from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

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
    "Eres un agente de voz breve. Usa generate_lorem_ipsum cuando pidan texto "
    "lorem ipsum con una cantidad de caracteres, y sum_numbers cuando pidan sumar "
    "números. No inventes el resultado de esas tools: ejecútalas. Habla en español."
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


def describe_tool_start(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    if name == "generate_lorem_ipsum":
        count = arguments.get("characters", "?")
        return "Generando lorem ipsum", f"{count} caracteres"
    if name == "sum_numbers":
        values = arguments.get("numbers") or []
        return "Sumando números", f"{len(values)} valores"
    return name, "Ejecutando"


def describe_tool_done(name: str, arguments: dict[str, Any], result: str) -> tuple[str, str]:
    if name == "generate_lorem_ipsum":
        return "Lorem ipsum listo", f"{len(result)} caracteres · completado"
    if name == "sum_numbers":
        return "Suma lista", f"Total {result}"
    return name, "Completado"


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    if name == "generate_lorem_ipsum":
        return generate_lorem_ipsum(int(arguments.get("characters") or 1))
    if name == "sum_numbers":
        values = arguments.get("numbers") or []
        if not isinstance(values, list) or not values:
            raise ValueError("Necesito al menos un número para sumar.")
        return sum_numbers(values)
    raise ValueError(f"Tool desconocida: {name}")
