from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AgentSnapshot,
    Call,
    CallStatus,
    Conversation,
    Message,
    Objection,
    Opportunity,
    OpportunityStatus,
    Product,
    ProductInterest,
)


class AnalyticsRepository:
    """Explicit aggregate queries. Every query starts with the tenant and date scope."""

    @staticmethod
    async def get_conversation_count(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> int:
        value = await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.organization_id == organization_id,
                Conversation.created_at >= date_from,
                Conversation.created_at < date_to,
            )
        )
        return int(value or 0)

    @staticmethod
    async def get_final_agent_signals(
        session: AsyncSession,
        organization_id: uuid.UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict[str, Any]]:
        ended_conversations = (
            select(Call.conversation_id)
            .where(
                Call.organization_id == organization_id,
                Call.status == CallStatus.ENDED,
                Call.ended_at.is_not(None),
                Call.ended_at >= date_from,
                Call.ended_at < date_to,
            )
            .distinct()
        )
        rows = (
            await session.scalars(
                select(AgentSnapshot.data).where(
                    AgentSnapshot.organization_id == organization_id,
                    AgentSnapshot.conversation_id.in_(ended_conversations),
                )
            )
        ).all()
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    async def get_opportunity_summary(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> dict[str, int]:
        row = (
            await session.execute(
                select(
                    func.count(Opportunity.id).label("opportunities"),
                    func.count(case((Opportunity.status == OpportunityStatus.WON, Opportunity.id))).label("won"),
                    func.coalesce(func.sum(case((Opportunity.status == OpportunityStatus.WON, Opportunity.amount_minor), else_=0)), 0).label("revenue"),
                    func.count(case((_recovered_condition(), Opportunity.id))).label("recovered"),
                    func.count(case((_recovery_started_condition(), Opportunity.id))).label("recovery_opportunities"),
                    func.coalesce(func.sum(case((_recovered_condition(), Opportunity.amount_minor), else_=0)), 0).label("recovered_revenue"),
                ).where(
                    Opportunity.organization_id == organization_id,
                    Opportunity.created_at >= date_from,
                    Opportunity.created_at < date_to,
                )
            )
        ).one()
        return {
            "opportunities": int(row.opportunities or 0),
            "won": int(row.won or 0),
            "revenue": int(row.revenue or 0),
            "recovered": int(row.recovered or 0),
            "recovery_opportunities": int(row.recovery_opportunities or 0),
            "recovered_revenue": int(row.recovered_revenue or 0),
        }

    @staticmethod
    async def get_won_count(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> int:
        summary = await AnalyticsRepository.get_opportunity_summary(session, organization_id, date_from, date_to)
        return summary["won"]

    @staticmethod
    async def get_conversion(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> tuple[int, int, float]:
        summary = await AnalyticsRepository.get_opportunity_summary(session, organization_id, date_from, date_to)
        return summary["won"], summary["opportunities"], _rate(summary["won"], summary["opportunities"])

    @staticmethod
    async def get_revenue(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> int:
        return (await AnalyticsRepository.get_opportunity_summary(session, organization_id, date_from, date_to))["revenue"]

    @staticmethod
    async def get_recovery_metrics(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> dict[str, int | float]:
        summary = await AnalyticsRepository.get_opportunity_summary(session, organization_id, date_from, date_to)
        return {
            "recovered_sales": summary["recovered"],
            "recovery_opportunities": summary["recovery_opportunities"],
            "recovery_rate": _rate(summary["recovered"], summary["recovery_opportunities"]),
            "recovered_revenue_minor": summary["recovered_revenue"],
        }

    @staticmethod
    async def get_lost_reasons(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(Opportunity.lost_reason, func.count(Opportunity.id).label("count"))
                .where(
                    Opportunity.organization_id == organization_id,
                    Opportunity.status == OpportunityStatus.LOST,
                    Opportunity.lost_reason.is_not(None),
                    Opportunity.created_at >= date_from,
                    Opportunity.created_at < date_to,
                )
                .group_by(Opportunity.lost_reason)
                .order_by(func.count(Opportunity.id).desc(), Opportunity.lost_reason)
            )
        ).all()
        total = sum(int(row._mapping["count"]) for row in rows)
        return [
            {
                "reason": row.lost_reason,
                "count": int(row._mapping["count"]),
                "percentage": _rate(row._mapping["count"], total),
            }
            for row in rows
        ]

    @staticmethod
    async def get_objection_resolution(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> dict[str, int | float]:
        row = (
            await session.execute(
                select(
                    func.count(Objection.id).label("total"),
                    func.count(case((Objection.resolved.is_(True), Objection.id))).label("resolved"),
                ).where(
                    Objection.organization_id == organization_id,
                    Objection.created_at >= date_from,
                    Objection.created_at < date_to,
                )
            )
        ).one()
        total = int(row.total or 0)
        resolved = int(row.resolved or 0)
        return {"total": total, "resolved": resolved, "resolution_rate": _rate(resolved, total)}

    @staticmethod
    async def get_product_conversion(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        won_conversation = case((Opportunity.status == OpportunityStatus.WON, ProductInterest.conversation_id))
        rows = (
            await session.execute(
                select(
                    Product.id.label("product_id"),
                    Product.name.label("product_name"),
                    func.count(distinct(ProductInterest.conversation_id)).label("interested_count"),
                    func.count(distinct(won_conversation)).label("won_count"),
                )
                .join(ProductInterest, ProductInterest.product_id == Product.id)
                .outerjoin(
                    Opportunity,
                    and_(
                        Opportunity.organization_id == organization_id,
                        Opportunity.conversation_id == ProductInterest.conversation_id,
                        Opportunity.product_id == ProductInterest.product_id,
                        Opportunity.created_at >= date_from,
                        Opportunity.created_at < date_to,
                    ),
                )
                .where(
                    Product.organization_id == organization_id,
                    ProductInterest.organization_id == organization_id,
                    ProductInterest.created_at >= date_from,
                    ProductInterest.created_at < date_to,
                )
                .group_by(Product.id, Product.name)
                .order_by(Product.name)
            )
        ).all()
        return [
            {
                "product_id": row.product_id,
                "product_name": row.product_name,
                "interested_count": int(row.interested_count or 0),
                "won_count": int(row.won_count or 0),
                "conversion_rate": _rate(row.won_count, row.interested_count),
            }
            for row in rows
        ]

    @staticmethod
    async def get_conversion_trend(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        day = func.date_trunc("day", Opportunity.created_at).label("date")
        rows = (
            await session.execute(
                select(
                    day,
                    func.count(Opportunity.id).label("opportunities"),
                    func.count(case((Opportunity.status == OpportunityStatus.WON, Opportunity.id))).label("won"),
                )
                .where(
                    Opportunity.organization_id == organization_id,
                    Opportunity.created_at >= date_from,
                    Opportunity.created_at < date_to,
                )
                .group_by(day)
                .order_by(day)
            )
        ).all()
        return [
            {"date": row.date, "opportunities": int(row.opportunities), "won": int(row.won), "conversion_rate": _rate(row.won, row.opportunities)}
            for row in rows
        ]

    @staticmethod
    async def get_recovery_trend(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        day = func.date_trunc("day", Opportunity.recovery_started_at).label("date")
        rows = (await session.execute(select(day, func.count(Opportunity.id).label("started"), func.count(case((_recovered_condition(), Opportunity.id))).label("recovered")).where(Opportunity.organization_id == organization_id, _recovery_started_condition(), Opportunity.recovery_started_at >= date_from, Opportunity.recovery_started_at < date_to).group_by(day).order_by(day))).all()
        return [{"date": row.date, "recovery_rate": _rate(row.recovered, row.started)} for row in rows]

    @staticmethod
    async def get_metric_trend(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        conversation_day = func.date_trunc("day", Conversation.created_at).label("date")
        conversation_rows = (await session.execute(select(conversation_day, func.count(Conversation.id).label("conversations")).where(Conversation.organization_id == organization_id, Conversation.created_at >= date_from, Conversation.created_at < date_to).group_by(conversation_day))).all()
        opportunity_day = func.date_trunc("day", Opportunity.created_at).label("date")
        opportunity_rows = (await session.execute(select(opportunity_day, func.count(Opportunity.id).label("opportunities"), func.count(case((Opportunity.status == OpportunityStatus.WON, Opportunity.id))).label("won"), func.coalesce(func.sum(case((Opportunity.status == OpportunityStatus.WON, Opportunity.amount_minor), else_=0)), 0).label("revenue"), func.count(case((_recovered_condition(), Opportunity.id))).label("recovered"), func.coalesce(func.sum(case((_recovered_condition(), Opportunity.amount_minor), else_=0)), 0).label("recovered_revenue")).where(Opportunity.organization_id == organization_id, Opportunity.created_at >= date_from, Opportunity.created_at < date_to).group_by(opportunity_day))).all()
        buckets: dict[datetime, dict[str, int]] = defaultdict(lambda: {"conversations": 0, "opportunities": 0, "won": 0, "revenue_minor": 0, "recovered_sales": 0, "recovered_revenue_minor": 0})
        for row in conversation_rows:
            buckets[row.date]["conversations"] = int(row.conversations or 0)
        for row in opportunity_rows:
            bucket = buckets[row.date]
            bucket.update(opportunities=int(row.opportunities or 0), won=int(row.won or 0), revenue_minor=int(row.revenue or 0), recovered_sales=int(row.recovered or 0), recovered_revenue_minor=int(row.recovered_revenue or 0))
        return [{"date": date, **buckets[date]} for date in sorted(buckets)]

    @staticmethod
    async def get_objection_categories(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        rows = (await session.execute(select(Objection.category, func.count(case((Objection.resolved.is_(True), Objection.id))).label("resolved"), func.count(case((Objection.resolved.is_(False), Objection.id))).label("unresolved")).where(Objection.organization_id == organization_id, Objection.created_at >= date_from, Objection.created_at < date_to).group_by(Objection.category).order_by(func.count(Objection.id).desc()))).all()
        return [{"category": row.category, "resolved": int(row.resolved or 0), "unresolved": int(row.unresolved or 0), "resolution_rate": _rate(row.resolved, (row.resolved or 0) + (row.unresolved or 0))} for row in rows]

    @staticmethod
    async def get_objection_product_heatmap(session: AsyncSession, organization_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        interest_pairs = (
            select(ProductInterest.conversation_id, ProductInterest.product_id)
            .where(
                ProductInterest.organization_id == organization_id,
                ProductInterest.created_at >= date_from,
                ProductInterest.created_at < date_to,
            )
            .distinct()
            .subquery()
        )
        rows = (await session.execute(select(Objection.category, Product.name.label("product_name"), func.count(distinct(Objection.id)).label("count"), func.count(distinct(case((Objection.resolved.is_(True), Objection.id)))).label("resolved")).join(interest_pairs, interest_pairs.c.conversation_id == Objection.conversation_id).join(Product, and_(Product.id == interest_pairs.c.product_id, Product.organization_id == organization_id)).where(Objection.organization_id == organization_id, Objection.created_at >= date_from, Objection.created_at < date_to).group_by(Objection.category, Product.name).order_by(Objection.category, Product.name))).all()
        return [{"category": row.category, "product_name": row.product_name, "count": int(row._mapping["count"] or 0), "resolved": int(row.resolved or 0), "resolution_rate": _rate(row.resolved, row._mapping["count"])} for row in rows]


def _rate(numerator: int | None, denominator: int | None) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator or 0) * 100 / float(denominator), 2)


def _recovery_started_condition():
    return Opportunity.recovery_started_at.is_not(None)


def _recovered_condition():
    return and_(_recovery_started_condition(), Opportunity.status == OpportunityStatus.WON, Opportunity.recovered_at.is_not(None))


__all__ = ["AnalyticsRepository"]
