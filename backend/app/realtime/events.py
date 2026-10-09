from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RealtimeEvent(BaseModel):
    """JSON-safe event envelope shared by realtime subscribers."""

    type: str = Field(min_length=1)
    organization_id: uuid.UUID
    conversation_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
