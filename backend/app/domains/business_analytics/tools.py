from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from ...analytics.service import AnalyticsService


class BusinessAnalyticsArgs(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    selector: Literal["overview", "recovery", "losses", "products", "objections"] = "overview"


def _bounds(args: BusinessAnalyticsArgs) -> tuple[datetime, datetime]:
    end_date = args.date_to or datetime.now(UTC).date()
    start_date = args.date_from or end_date - timedelta(days=30)
    if end_date < start_date:
        raise ValueError("date_to must not be before date_from")
    return (
        datetime.combine(start_date, time.min, tzinfo=UTC),
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC),
    )


async def get_business_analytics(args: BusinessAnalyticsArgs, context: ToolContext) -> dict:
    if not context.organization_id:
        raise ValueError("An organization is required for analytics")
    organization_id = uuid.UUID(context.organization_id)
    date_from, date_to = _bounds(args)
    dashboard = await AnalyticsService().get_business_analytics(
        organization_id=organization_id,
        date_from=date_from,
        date_to=date_to,
    )
    if args.selector == "recovery":
        return {"summary": dashboard.summary.model_dump(mode="json", include={"recovered_sales", "recovery_opportunities", "recovery_rate", "recovered_revenue_minor"})}
    if args.selector == "losses":
        return {"lost_reasons": [item.model_dump(mode="json") for item in dashboard.lost_reasons]}
    if args.selector == "products":
        return {"products": [item.model_dump(mode="json") for item in dashboard.products]}
    if args.selector == "objections":
        return {"objections": dashboard.objections.model_dump(mode="json")}
    return {"summary": dashboard.summary.model_dump(mode="json"), "period": dashboard.period.model_dump(mode="json", by_alias=True)}


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="get_business_analytics",
            description="Read deterministic commercial analytics for the authenticated organization.",
            args_model=BusinessAnalyticsArgs,
            handler=get_business_analytics,
            side_effects="read",
            timeout_s=10.0,
        )
    )
