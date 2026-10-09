from __future__ import annotations

import asyncio
import inspect
import logging
import json
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .contracts import ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._frozen = False
        self.domain_questions: dict = {}
        self.context_instructions: list[str] = []

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
        clean = args.model_dump(mode='json')
        from ..policy import active_turn
        from ..state import Fact
        turn = active_turn.get()
        if turn:
            turn.store.state.phase = 'searching'
            turn.store.state.goal = turn.store.state.goal or name
        try:
            if inspect.iscoroutinefunction(definition.handler):
                result = await asyncio.wait_for(definition.handler(args, context), timeout=definition.timeout_s)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(definition.handler, args, context), timeout=definition.timeout_s
                )
            outcome = result if isinstance(result, ToolResult) else ToolResult(True, data=result)
            json.dumps(outcome.data, allow_nan=False)  # Validate at the tool boundary, before committing evidence.
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            outcome = ToolResult(False, error_code="TOOL_TIMEOUT", message="Tool timed out.")
        except Exception:
            logger.exception("Tool execution failed request_id=%s tool=%s", context.request_id, name)
            outcome = ToolResult(False, error_code="TOOL_EXECUTION_ERROR", message="Tool execution failed.")
        logger.info("Tool outcome request_id=%s tool=%s ok=%s error=%s",
                    context.request_id, name, outcome.ok, outcome.error_code)
        if turn:
            store, state = turn.store, turn.store.state
            state.tool_history = [*state.tool_history[-19:], {'tool': name, 'arguments': clean,
                'ok': outcome.ok, 'result': outcome.data if len(json.dumps(outcome.data)) <= 6000 else '[result too large; omitted]',
                'error': outcome.error_code, 'turn_id': turn.turn_id}]
            if outcome.ok:
                for key, value in clean.items():
                    state.facts[f'{name}.{key}'] = Fact(value=value, source=f'tool:{name}:{turn.turn_id}')
            state.phase = 'presenting' if outcome.ok else 'understanding'
            state.action('tool_completed' if outcome.ok else 'tool_failed', tool=name)
            await store.save()
        return outcome
