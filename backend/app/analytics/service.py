from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.session import get_session_factory
from .cache import AnalyticsCache, dashboard_key
from .repository import AnalyticsRepository, _rate
from .schemas import (
    AgentSignalsAggregate,
    AnalyticsPeriod,
    AnalyticsSummary,
    ConversionTrendPoint,
    DashboardComparison,
    DashboardResponse,
    FunnelPoint,
    LostReasonPoint,
    MetricDelta,
    MetricTrendPoint,
    ObjectionProductPoint,
    ObjectionCategoryPoint,
    ObjectionMetrics,
    ProductConversionPoint,
    RecoveryTrendPoint,
)


_SIGNAL_DEFAULTS = {
    "satisfaction": "unknown",
    "frustration": "unknown",
    "fluency": "unknown",
    "emotion": "unknown",
    "intent": "unknown",
}
_DISPLAY_SIGNAL_KEYS = {*_SIGNAL_DEFAULTS, "integrity"}


def _aggregate_agent_signals(snapshots: list[dict]) -> AgentSignalsAggregate | None:
    totals: dict[str, dict[str, float]] = {}
    observations: dict[str, int] = {}
    sample_count = 0
    for snapshot in snapshots:
        signals = snapshot.get("signals")
        if not isinstance(signals, dict) or not any(key in signals for key in _DISPLAY_SIGNAL_KEYS):
            continue
        sample_count += 1
        for key, raw_signal in signals.items():
            if key not in _DISPLAY_SIGNAL_KEYS:
                continue
            if not isinstance(raw_signal, dict):
                continue
            value = raw_signal.get("value")
            if not isinstance(value, str) or not value:
                continue
            raw_probabilities = raw_signal.get("probabilities")
            probabilities = {
                str(label): float(probability)
                for label, probability in raw_probabilities.items()
                if isinstance(raw_probabilities, dict)
                and isinstance(label, str)
                and isinstance(probability, (int, float))
                and probability >= 0
            } if isinstance(raw_probabilities, dict) else {}
            mass = sum(probabilities.values())
            if mass <= 0:
                probabilities = {value: 1.0}
                mass = 1.0
            bucket = totals.setdefault(key, {})
            for label, probability in probabilities.items():
                bucket[label] = bucket.get(label, 0.0) + probability / mass
            observations[key] = observations.get(key, 0) + 1

    if sample_count == 0:
        return None

    aggregated: dict[str, dict] = {}
    for key, bucket in totals.items():
        count = observations[key]
        probabilities = {label: round(total / count, 6) for label, total in bucket.items()}
        value = max(probabilities, key=lambda label: (probabilities[label], label))
        aggregated[key] = {"value": value, "probabilities": probabilities}
    for key, value in _SIGNAL_DEFAULTS.items():
        aggregated.setdefault(key, {"value": value, "probabilities": {value: 1.0}})
    return AgentSignalsAggregate.model_validate({"sample_count": sample_count, "signals": aggregated})


class AnalyticsService:
    def __init__(self, cache: AnalyticsCache | None = None, repository: AnalyticsRepository | None = None) -> None:
        self.cache = cache or AnalyticsCache()
        self.repository = repository or AnalyticsRepository()

    async def _agent_signal_aggregate(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> AgentSignalsAggregate | None:
        if getattr(type(self.repository), "get_final_agent_signals", None) is None:
            return None
        snapshots = await self.repository.get_final_agent_signals(
            session, organization_id, date_from, date_to
        )
        return _aggregate_agent_signals(snapshots)

    async def dashboard(self, session: AsyncSession, *, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> DashboardResponse:
        version = await self.cache.version(session, organization_id)
        key = dashboard_key(organization_id, version, date_from, date_to)
        cached = await self.cache.get(key)
        if cached is not None:
            response = DashboardResponse.model_validate(cached)
            response.agent_signals = await self._agent_signal_aggregate(
                session, organization_id, date_from, date_to
            )
            return response

        async def build_summary(period_from: datetime, period_to: datetime) -> AnalyticsSummary:
            conversations = await self.repository.get_conversation_count(session, organization_id, period_from, period_to)
            summary_data = await self.repository.get_opportunity_summary(session, organization_id, period_from, period_to)
            return AnalyticsSummary(
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

        summary = await build_summary(date_from, date_to)
        period_length = date_to - date_from
        previous_summary = await build_summary(date_from - period_length, date_from)
        trend = await self.repository.get_conversion_trend(session, organization_id, date_from, date_to)
        recovery_trend = await self.repository.get_recovery_trend(session, organization_id, date_from, date_to)
        metric_trend = await self.repository.get_metric_trend(session, organization_id, date_from, date_to)
        lost_reasons = await self.repository.get_lost_reasons(session, organization_id, date_from, date_to)
        objections = await self.repository.get_objection_resolution(session, organization_id, date_from, date_to)
        products = await self.repository.get_product_conversion(session, organization_id, date_from, date_to)
        objection_categories = await self.repository.get_objection_categories(session, organization_id, date_from, date_to)
        funnel_values = [
            ("Conversaciones", summary.conversations),
            ("Oportunidades", summary.opportunities),
            ("Won", summary.won),
        ]
        funnel = [
            {"stage": stage, "value": value, "percentage": _rate(value, summary.conversations)}
            for stage, value in funnel_values
        ]
        objection_product_heatmap = await self.repository.get_objection_product_heatmap(session, organization_id, date_from, date_to)
        agent_signals = await self._agent_signal_aggregate(
            session, organization_id, date_from, date_to
        )

        def delta(current: float, previous: float) -> MetricDelta:
            absolute = current - previous
            percentage = None if previous == 0 else round(absolute * 100 / abs(previous), 2)
            return MetricDelta(current=current, previous=previous, absolute=absolute, percentage=percentage)

        comparison = DashboardComparison(
            revenue_minor=delta(summary.revenue_minor, previous_summary.revenue_minor),
            won=delta(summary.won, previous_summary.won),
            conversion_rate=delta(summary.conversion_rate, previous_summary.conversion_rate),
            recovered_revenue_minor=delta(summary.recovered_revenue_minor, previous_summary.recovered_revenue_minor),
            recovery_rate=delta(summary.recovery_rate, previous_summary.recovery_rate),
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
            comparison=comparison,
            metric_trend=[MetricTrendPoint.model_validate(item) for item in metric_trend],
            funnel=[FunnelPoint.model_validate(item) for item in funnel],
            objection_product_heatmap=[ObjectionProductPoint.model_validate(item) for item in objection_product_heatmap],
            agent_signals=agent_signals,
        )
        await self.cache.set(key, response.model_dump(mode="json", by_alias=True))
        return response

    async def get_business_analytics(self, *, organization_id: uuid.UUID, date_from: datetime, date_to: datetime, session: AsyncSession | None = None) -> DashboardResponse:
        if session is not None:
            return await self.dashboard(session, organization_id=organization_id, date_from=date_from, date_to=date_to)
        async with get_session_factory()() as owned_session:
            return await self.dashboard(owned_session, organization_id=organization_id, date_from=date_from, date_to=date_to)


__all__ = ["AnalyticsService"]
