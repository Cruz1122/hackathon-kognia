from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.session import get_session_factory
from .cache import AnalyticsCache, dashboard_key
from .repository import AnalyticsRepository, _rate
from .schemas import (
    AnalyticsPeriod,
    AnalyticsSummary,
    ConversionTrendPoint,
    DashboardResponse,
    LostReasonPoint,
    ObjectionCategoryPoint,
    ObjectionMetrics,
    ProductConversionPoint,
    RecoveryTrendPoint,
)


class AnalyticsService:
    def __init__(self, cache: AnalyticsCache | None = None, repository: AnalyticsRepository | None = None) -> None:
        self.cache = cache or AnalyticsCache()
        self.repository = repository or AnalyticsRepository()

    async def dashboard(self, session: AsyncSession, *, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> DashboardResponse:
        version = await self.cache.version(session, organization_id)
        key = dashboard_key(organization_id, version, date_from, date_to)
        cached = await self.cache.get(key)
        if cached is not None:
            return DashboardResponse.model_validate(cached)

        conversations = await self.repository.get_conversation_count(session, organization_id, date_from, date_to)
        summary_data = await self.repository.get_opportunity_summary(session, organization_id, date_from, date_to)
        trend = await self.repository.get_conversion_trend(session, organization_id, date_from, date_to)
        recovery_trend = await self.repository.get_recovery_trend(session, organization_id, date_from, date_to)
        lost_reasons = await self.repository.get_lost_reasons(session, organization_id, date_from, date_to)
        objections = await self.repository.get_objection_resolution(session, organization_id, date_from, date_to)
        products = await self.repository.get_product_conversion(session, organization_id, date_from, date_to)
        objection_categories = await self.repository.get_objection_categories(session, organization_id, date_from, date_to)
        summary = AnalyticsSummary(
            conversations=conversations,
            opportunities=summary_data["opportunities"],
            won=summary_data["won"],
            conversion_rate=_rate(summary_data["won"], summary_data["opportunities"]),
            revenue_minor=summary_data["revenue"],
            recovered_sales=summary_data["recovered"],
            recovery_opportunities=summary_data["recovery_opportunities"],
            recovery_rate=_rate(summary_data["recovered"], summary_data["recovery_opportunities"]),
            recovered_revenue_minor=summary_data["recovered_revenue"],
        )
        response = DashboardResponse(
            period=AnalyticsPeriod.model_validate({"from": date_from, "to": date_to}),
            summary=summary,
            conversion_trend=[ConversionTrendPoint.model_validate(item) for item in trend],
            recovery_trend=[RecoveryTrendPoint.model_validate(item) for item in recovery_trend],
            lost_reasons=[LostReasonPoint.model_validate(item) for item in lost_reasons],
            objections=ObjectionMetrics.model_validate(objections),
            objection_categories=[ObjectionCategoryPoint.model_validate(item) for item in objection_categories],
            products=[ProductConversionPoint.model_validate(item) for item in products],
        )
        await self.cache.set(key, response.model_dump(mode="json", by_alias=True))
        return response

    async def get_business_analytics(self, *, organization_id: uuid.UUID, date_from: datetime, date_to: datetime, session: AsyncSession | None = None) -> DashboardResponse:
        if session is not None:
            return await self.dashboard(session, organization_id=organization_id, date_from=date_from, date_to=date_to)
        async with get_session_factory()() as owned_session:
            return await self.dashboard(owned_session, organization_id=organization_id, date_from=date_from, date_to=date_to)


__all__ = ["AnalyticsService"]
