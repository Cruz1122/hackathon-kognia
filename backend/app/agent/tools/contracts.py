from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., Any]
    side_effects: Literal["read", "write"]
    timeout_s: float = 5.0
    replay_safe: bool = False  # Only pure/local effects committed in the operation ledger.

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("Tool name must be non-empty")
        if self.timeout_s <= 0 or not math.isfinite(self.timeout_s):
            raise ValueError("Tool timeout must be finite and positive")

    @property
    def schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


@dataclass(frozen=True)
class ToolContext:
    request_id: str
    conversation_id: str | None = None
    organization_id: str | None = None
    user_id: str | None = None
    channel: str = 'voice'
    operation_id: str | None = None
    system_initiated: bool = False


@dataclass
class ToolResult:
    ok: bool
    data: dict | list | str | None = None
    error_code: str | None = None
    message: str | None = None
