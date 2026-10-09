from __future__ import annotations

import importlib
import os

from .registry import ToolRegistry


def load_tool_registry(value: str | None = None) -> ToolRegistry:
    modules = [item.strip() for item in (value or os.getenv("AGENT_TOOL_MODULES", "app.domains.ips.tools")).split(",") if item.strip()]
    registry = ToolRegistry()
    for path in modules:
        module = importlib.import_module(path)
        register = getattr(module, "register_tools", None)
        if not callable(register):
            raise RuntimeError(f"Configured tool module has no register_tools: {path}")
        register(registry)
        registry.domain_questions.update(getattr(module, 'JEV_QUESTIONS', {}))
        instructions = getattr(module, 'CONTEXT_INSTRUCTIONS', '')
        if instructions:
            registry.context_instructions.append(instructions)
    registry.freeze()
    return registry
