from __future__ import annotations

import asyncio
import inspect
import logging
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .contracts import ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._frozen = False

    def register(self, definition: ToolDefinition) -> None:
        if self._frozen:
            raise RuntimeError("Tool registry is frozen")
        if definition.name in self._definitions:
            raise ValueError(f"TOOL_DUPLICATE_NAME: {definition.name}")
        self._definitions[definition.name] = definition

    def freeze(self) -> None:
        self._frozen = True

    @property
    def definitions(self) -> MappingProxyType[str, ToolDefinition]:
        return MappingProxyType(self._definitions)

    def list_definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def resolve(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": d.name, "description": d.description, "parameters": d.schema} for d in self._definitions.values()]

    async def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        definition = self.resolve(name)
        if definition is None:
            return ToolResult(False, error_code="TOOL_NOT_FOUND", message="Tool not found.")
        try:
            args = definition.args_model.model_validate(arguments)
        except ValidationError:
            return ToolResult(False, error_code="TOOL_ARGUMENT_VALIDATION_ERROR", message="Invalid tool arguments.")
        try:
            if inspect.iscoroutinefunction(definition.handler):
                result = await asyncio.wait_for(definition.handler(args, context), timeout=definition.timeout_s)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(definition.handler, args, context), timeout=definition.timeout_s
                )
            if isinstance(result, ToolResult):
                return result
            return ToolResult(True, data=result)
        except asyncio.TimeoutError:
            return ToolResult(False, error_code="TOOL_TIMEOUT", message="Tool timed out.")
        except Exception:
            logger.exception("Tool execution failed request_id=%s tool=%s", context.request_id, name)
            return ToolResult(False, error_code="TOOL_EXECUTION_ERROR", message="Tool execution failed.")
