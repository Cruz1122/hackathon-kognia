from __future__ import annotations

import importlib
import os

from .registry import ToolRegistry


def load_tool_registry(value: str | None = None) -> ToolRegistry:
    modules = [item.strip() for item in (value or os.getenv("AGENT_TOOL_MODULES", "app.domains.demo_booking.tools,app.domains.business_analytics.tools")).split(",") if item.strip()]
    registry = ToolRegistry()
    for path in modules:
        module = importlib.import_module(path)
        register = getattr(module, "register_tools", None)
        if not callable(register):
            raise RuntimeError(f"Configured tool module has no register_tools: {path}")
        register(registry)
    if value is None:
        from .legacy import register_tools
        register_tools(registry)
    registry.freeze()
    return registry
