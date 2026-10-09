from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.analytics.cache import AnalyticsCache
from app.analytics.service import AnalyticsService
from app.db.models import (
    Conversation,
    Customer,
    Objection,
    Opportunity,
    OpportunityStatus,
    Organization,
    Product,
    ProductInterest,
    User,
    UserRole,
)
from app.db.session import dispose_engine, get_session_factory


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL analytics integration tests",
)


class NoopCache:
    async def version(self, _session, _organization_id: uuid.UUID) -> int:
        return 0

    async def get(self, _key: str) -> None:
        return None

    async def set(self, _key: str, _payload: dict) -> bool:
        return True


def _timestamp(day: int) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_dashboard_formulas_and_tenant_isolation_against_postgres() -> None:
    factory = get_session_factory()
    async with factory() as session:
        organization = Organization(name="Analytics A", slug=f"analytics-a-{uuid.uuid4()}")
        other_organization = Organization(name="Analytics B", slug=f"analytics-b-{uuid.uuid4()}")
        admin = User(
            organization=organization,
            email=f"analytics-a-{uuid.uuid4()}@test.invalid",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        other_admin = User(
            organization=other_organization,
            email=f"analytics-b-{uuid.uuid4()}@test.invalid",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        product = Product(organization=organization, name=f"Analytics product {uuid.uuid4()}")
        other_product = Product(organization=other_organization, name=f"Other product {uuid.uuid4()}")
        session.add_all([organization, other_organization, admin, other_admin, product, other_product])
        await session.flush()

        conversations: list[Conversation] = []
        opportunities: list[Opportunity] = []
        interests: list[ProductInterest] = []
        for index in range(10):
            conversation = Conversation(
                organization_id=organization.id,
                created_by=admin.id,
                channel="voice",
                status="open",
                created_at=_timestamp(10),
            )
            conversations.append(conversation)
            session.add(conversation)
            await session.flush()
            status = OpportunityStatus.WON if index < 4 else OpportunityStatus.LOST
            opportunity = Opportunity(
                organization_id=organization.id,
                conversation_id=conversation.id,
                product_id=product.id,
                status=status,
                amount_minor=[100, 200, 300, 400][index] if index < 4 else None,
                lost_reason=("price" if index % 2 == 0 else "timing") if status == OpportunityStatus.LOST else None,
                created_at=_timestamp(10),
                updated_at=_timestamp(10),
                won_at=_timestamp(10) if status == OpportunityStatus.WON else None,
                lost_at=_timestamp(10) if status == OpportunityStatus.LOST else None,
                recovery_started_at=_timestamp(11) if index in {1, 3} else None,
                recovered_at=_timestamp(12) if index == 3 else None,
            )
            opportunities.append(opportunity)
            interests.append(
                ProductInterest(
                    organization_id=organization.id,
                    conversation_id=conversation.id,
                    product_id=product.id,
                    created_at=_timestamp(10),
                )
            )
            session.add_all([opportunity, interests[-1]])

        boundary_conversation = Conversation(
            organization_id=organization.id,
            created_by=admin.id,
            channel="voice",
            status="open",
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        other_conversation = Conversation(
            organization_id=other_organization.id,
            created_by=other_admin.id,
            channel="voice",
            status="open",
            created_at=_timestamp(10),
        )
        session.add_all([boundary_conversation, other_conversation])
        await session.flush()
        session.add(
            Opportunity(
                organization_id=other_organization.id,
                conversation_id=other_conversation.id,
                product_id=other_product.id,
                status=OpportunityStatus.WON,
                amount_minor=9999,
                won_at=_timestamp(10),
                created_at=_timestamp(10),
                updated_at=_timestamp(10),
            )
        )
        session.add_all(
            [
                Objection(
                    organization_id=organization.id,
                    conversation_id=conversations[index].id,
                    category="price",
                    resolved=index < 3,
                    created_at=_timestamp(10),
                )
                for index in range(5)
            ]
        )
        await session.flush()

        dashboard = await AnalyticsService(cache=NoopCache()).dashboard(
            session,
            organization_id=organization.id,
            date_from=datetime(2026, 9, 1, tzinfo=UTC),
            date_to=datetime(2026, 10, 1, tzinfo=UTC),
        )

        assert dashboard.summary.conversations == 10
        assert dashboard.summary.opportunities == 10
        assert dashboard.summary.won == 4
        assert dashboard.summary.conversion_rate == 40.0
        assert dashboard.summary.revenue_minor == 1000
        assert dashboard.summary.recovered_sales == 1
        assert dashboard.summary.recovery_opportunities == 2
        assert dashboard.summary.recovery_rate == 50.0
        assert dashboard.summary.recovered_revenue_minor == 400
        assert dashboard.objections.total == 5
        assert dashboard.objections.resolved == 3
        assert dashboard.products[0].interested_count == 10
        assert dashboard.products[0].won_count == 4
        assert dashboard.products[0].conversion_rate == 40.0
        assert dashboard.metric_trend
        assert [item.stage for item in dashboard.funnel] == ["Conversaciones", "Oportunidades", "Won"]
        assert dashboard.objection_product_heatmap[0].product_name == product.name

        reasons = {item.reason: item.count for item in dashboard.lost_reasons}
        assert reasons == {"price": 3, "timing": 3}

    await dispose_engine()


@pytest.mark.asyncio
async def test_dashboard_falls_back_to_postgres_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.analytics import cache as cache_module

    monkeypatch.setattr(cache_module, "redis_get", lambda _key: _raise_redis())
    monkeypatch.setattr(cache_module, "redis_set", lambda *_args, **_kwargs: _raise_redis())
    assert await AnalyticsCache().get("analytics:test") is None
    assert await AnalyticsCache().set("analytics:test", {}) is False

    class Repository:
        async def get_conversation_count(self, *_args) -> int:
            return 2

        async def get_opportunity_summary(self, *_args) -> dict[str, int]:
            return {
                "opportunities": 2,
                "won": 1,
                "revenue": 400,
                "recovered": 0,
                "recovery_opportunities": 0,
                "recovered_revenue": 0,
            }

        async def get_conversion_trend(self, *_args) -> list:
            return []

        async def get_recovery_trend(self, *_args) -> list:
            return []

        async def get_metric_trend(self, *_args) -> list:
            return []

        async def get_lost_reasons(self, *_args) -> list:
            return []

        async def get_objection_resolution(self, *_args) -> dict:
            return {"total": 0, "resolved": 0, "resolution_rate": 0.0}

        async def get_product_conversion(self, *_args) -> list:
            return []

        async def get_objection_categories(self, *_args) -> list:
            return []

        async def get_objection_product_heatmap(self, *_args) -> list:
            return []

    session = AsyncMock()
    session.scalar.return_value = 0
    dashboard = await AnalyticsService(cache=AnalyticsCache(), repository=Repository()).dashboard(
        session,
        organization_id=uuid.uuid4(),
        date_from=datetime(2026, 9, 1, tzinfo=UTC),
        date_to=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert dashboard.summary.revenue_minor == 400


async def _raise_redis(*_args, **_kwargs):
    raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_postgres_rejects_cross_tenant_commercial_references() -> None:
    factory = get_session_factory()
    async with factory() as session:
        organization = Organization(name="Tenant A", slug=f"tenant-a-{uuid.uuid4()}")
        other_organization = Organization(name="Tenant B", slug=f"tenant-b-{uuid.uuid4()}")
        admin = User(
            organization=organization,
            email=f"tenant-a-{uuid.uuid4()}@test.invalid",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        other_admin = User(
            organization=other_organization,
            email=f"tenant-b-{uuid.uuid4()}@test.invalid",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        session.add_all([organization, other_organization, admin, other_admin])
        await session.flush()
        conversation = Conversation(
            organization_id=organization.id,
            created_by=admin.id,
            channel="voice",
            status="open",
        )
        other_customer = Customer(organization_id=other_organization.id, name="Other tenant")
        product = Product(organization_id=organization.id, name=f"Tenant product {uuid.uuid4()}")
        session.add_all([conversation, other_customer, product])
        await session.flush()

        session.add(
            Opportunity(
                organization_id=organization.id,
                conversation_id=conversation.id,
                customer_id=other_customer.id,
                product_id=product.id,
                status=OpportunityStatus.PENDING,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    await dispose_engine()
