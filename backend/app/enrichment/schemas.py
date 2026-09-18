from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ObjectionInsight(BaseModel):
    category: str = Field(pattern="^(price|competitor|timing|trust|features|other)$")
    resolved: bool


class ProductInterestInsight(BaseModel):
    product_id: uuid.UUID


class EnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lost_reason: str | None = Field(default=None, max_length=64)
    objections: list[ObjectionInsight] = Field(default_factory=list, max_length=50)
    product_interests: list[ProductInterestInsight] = Field(default_factory=list, max_length=50)
