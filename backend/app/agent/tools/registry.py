from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
import json
from dataclasses import replace
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
        from ..policy import active_turn, authorize
        from ..state import Fact, fingerprint
        from ...db.models import AgentOperation

        turn = active_turn.get()
        operation = None
        clean = args.model_dump(mode='json')
        if turn and definition.side_effects == 'read':
            state = turn.store.state
            if state.pending and any(k in state.pending.arguments and state.pending.arguments[k] != v for k, v in clean.items()):
                state.pending = None
                state.authorized = None
                state.action('requirements_changed')
            state.phase = 'searching'
            state.goal = state.goal or name
        if definition.side_effects == 'write':
            if turn is None:
                return ToolResult(False, error_code='CONFIRMATION_REQUIRED', message='A persistent conversation and explicit confirmation are required.')
            store, state = turn.store, turn.store.state
            if not authorize(state, name, clean):
                await store.save()
                return ToolResult(False, error_code='CONFIRMATION_REQUIRED', message='Present the exact pending proposal and ask the customer to reply: confirmo.')
            operation_id = fingerprint(state.conversation_id, {'proposal': state.pending.fingerprint})
            operation = await store.db.get(AgentOperation, operation_id)
            if operation:
                if operation.status == 'succeeded':
                    state.pending = None
                    state.authorized = None
                    state.phase = 'completed'
                    await store.save()
                    return ToolResult(True, data=operation.result)
                if not definition.replay_safe:
                    return ToolResult(False, error_code='OPERATION_UNCERTAIN', message='The previous attempt requires provider reconciliation. Do not retry it.')
                operation.status = 'started'
            else:
                operation = AgentOperation(id=operation_id, conversation_id=uuid.UUID(state.conversation_id),
                    organization_id=uuid.UUID(state.organization_id), tool=name, arguments=clean, status='started')
                store.db.add(operation)
            await store.save()  # intent survives cancellation or a provider timeout
            context = replace(context, operation_id=operation_id)
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
            if turn and operation:
                operation.status = 'uncertain'
                turn.store.state.authorized = None
                turn.store.state.action('action_uncertain', operation_id=operation.id, reason='cancelled')
                await turn.store.save()
            raise
        except asyncio.TimeoutError:
            outcome = ToolResult(False, error_code="TOOL_TIMEOUT", message="Tool timed out.")
        except Exception:
            logger.exception("Tool execution failed request_id=%s tool=%s", context.request_id, name)
            outcome = ToolResult(False, error_code="TOOL_EXECUTION_ERROR", message="Tool execution failed.")
        if turn:
            store, state = turn.store, turn.store.state
            state.tool_history = [*state.tool_history[-19:], {'tool': name, 'arguments': clean,
                'ok': outcome.ok, 'result': outcome.data if len(json.dumps(outcome.data)) <= 6000 else '[result too large; omitted]',
                'error': outcome.error_code, 'turn_id': turn.turn_id}]
            if outcome.ok:
                for key, value in clean.items():
                    state.facts[f'{name}.{key}'] = Fact(value=value, source=f'tool:{name}:{turn.turn_id}')
            if operation:
                # A signed provider webhook may have reconciled the operation meanwhile.
                await store.db.refresh(operation)
                if operation.status == 'succeeded' and not outcome.ok:
                    outcome = ToolResult(True, data=operation.result)
                operation.status = 'succeeded' if outcome.ok else 'uncertain'
                operation.result = outcome.data if isinstance(outcome.data, dict) else {'value': outcome.data}
                state.authorized = None
                if outcome.ok:
                    state.pending = None
                    state.phase = 'completed'
                    state.action('action_completed', tool=name, operation_id=operation.id)
            else:
                state.phase = 'presenting' if outcome.ok else 'understanding'
                state.action('tool_completed' if outcome.ok else 'tool_failed', tool=name)
            await store.save()
        return outcome
