from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsPeriod(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = ConfigDict(populate_by_name=True)


class AnalyticsSummary(BaseModel):
    conversations: int = 0
    opportunities: int = 0
    won: int = 0
    conversion_rate: float = 0.0
    revenue_minor: int = 0
    recovered_sales: int = 0
    recovery_opportunities: int = 0
    recovery_rate: float = 0.0
    recovered_revenue_minor: int = 0


class ConversionTrendPoint(BaseModel):
    date: datetime
    opportunities: int
    won: int
    conversion_rate: float


class RecoveryTrendPoint(BaseModel):
    date: datetime
    recovery_rate: float


class ObjectionCategoryPoint(BaseModel):
    category: str
    resolved: int
    unresolved: int
    resolution_rate: float


class LostReasonPoint(BaseModel):
    reason: str
    count: int
    percentage: float


class ObjectionMetrics(BaseModel):
    total: int = 0
    resolved: int = 0
    resolution_rate: float = 0.0


class ProductConversionPoint(BaseModel):
    product_id: uuid.UUID
    product_name: str
    interested_count: int
    won_count: int
    conversion_rate: float


class DashboardResponse(BaseModel):
    period: AnalyticsPeriod
    summary: AnalyticsSummary
    conversion_trend: list[ConversionTrendPoint] = Field(default_factory=list)
    recovery_trend: list[RecoveryTrendPoint] = Field(default_factory=list)
    lost_reasons: list[LostReasonPoint] = Field(default_factory=list)
    objections: ObjectionMetrics
    objection_categories: list[ObjectionCategoryPoint] = Field(default_factory=list)
    products: list[ProductConversionPoint] = Field(default_factory=list)
