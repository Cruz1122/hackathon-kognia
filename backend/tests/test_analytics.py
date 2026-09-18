from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import httpx

from app.analytics.repository import _rate
from app.analytics.cache import AnalyticsCache, bump_version
from app.analytics.schemas import DashboardResponse
from app.analytics.service import AnalyticsService
import app.analytics.router as analytics_router_module
from app import main
from app.auth.tokens import create_access_token
from app.auth.dependencies import get_current_user
from app.db.models import User, UserRole
from app.db.session import get_db


def _period() -> tuple[datetime, datetime]:
    return datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cache_version_comes_from_postgres_revision() -> None:
    session = AsyncMock()
    session.scalar.return_value = 12
    organization_id = uuid.uuid4()

    assert await AnalyticsCache().version(session, organization_id) == 12
    statement = session.scalar.await_args.args[0]
    assert "organizations.analytics_version" in str(statement)


@pytest.mark.asyncio
async def test_bump_version_is_part_of_the_business_transaction() -> None:
    session = AsyncMock()
    organization_id = uuid.uuid4()

    await bump_version(session, organization_id)

    statement = session.execute.await_args.args[0]
    assert "analytics_version" in str(statement)
    assert session.commit.await_count == 0


class FakeCache:
    def __init__(self, cached: dict | None = None) -> None:
        self.cached = cached
        self.writes: list[tuple[str, dict]] = []

    async def version(self, _session, organization_id: uuid.UUID) -> int:
        del organization_id
        return 3

    async def get(self, key: str) -> dict | None:
        del key
        return self.cached

    async def set(self, key: str, payload: dict) -> bool:
        self.writes.append((key, payload))
        return True


class FakeRepository:
    async def get_conversation_count(self, *args) -> int:
        return 10

    async def get_opportunity_summary(self, *args) -> dict[str, int]:
        return {"opportunities": 10, "won": 4, "revenue": 1000, "recovered": 1, "recovery_opportunities": 2, "recovered_revenue": 400}

    async def get_conversion_trend(self, *args) -> list:
        return []

    async def get_lost_reasons(self, *args) -> list:
        return [{"reason": "price", "count": 6, "percentage": 100.0}]

    async def get_objection_resolution(self, *args) -> dict:
        return {"total": 5, "resolved": 3, "resolution_rate": 60.0}

    async def get_product_conversion(self, *args) -> list:
        return []


@pytest.mark.asyncio
async def test_dashboard_calculates_controlled_business_numbers() -> None:
    organization_id = uuid.uuid4()
    date_from, date_to = _period()
    service = AnalyticsService(cache=FakeCache(), repository=FakeRepository())
    response = await service.dashboard(AsyncMock(), organization_id=organization_id, date_from=date_from, date_to=date_to)
    assert response.summary.model_dump() == {
        "conversations": 10,
        "opportunities": 10,
        "won": 4,
        "conversion_rate": 40.0,
        "revenue_minor": 1000,
        "recovered_sales": 1,
        "recovery_opportunities": 2,
        "recovery_rate": 50.0,
        "recovered_revenue_minor": 400,
    }
    assert response.lost_reasons[0].percentage == 100.0
    assert response.objections.resolution_rate == 60.0


@pytest.mark.asyncio
async def test_dashboard_cache_hit_skips_repository() -> None:
    cached = DashboardResponse(
        period={"from": _period()[0], "to": _period()[1]},
        summary={},
        objections={},
    ).model_dump(mode="json")
    repository = AsyncMock()
    service = AnalyticsService(cache=FakeCache(cached), repository=repository)
    result = await service.dashboard(AsyncMock(), organization_id=uuid.uuid4(), date_from=_period()[0], date_to=_period()[1])
    assert result.summary.conversations == 0
    repository.get_conversation_count.assert_not_awaited()


def test_rates_are_zero_for_empty_denominators() -> None:
    assert _rate(4, 10) == 40.0
    assert _rate(0, 0) == 0.0


@pytest.mark.asyncio
async def test_dashboard_endpoint_requires_auth_and_scopes_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="analytics@test.invalid",
        password_hash="hash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session = AsyncMock()

    async def override_db():
        yield session

    async def override_user() -> User:
        return user

    observed: dict[str, object] = {}

    async def fake_dashboard(_session, *, organization_id, date_from, date_to):
        observed.update(organization_id=organization_id, date_from=date_from, date_to=date_to)
        return DashboardResponse(period={"from": date_from, "to": date_to}, summary={}, objections={})

    monkeypatch.setattr(analytics_router_module, "analytics_service", type("Service", (), {"dashboard": staticmethod(fake_dashboard)})())
    try:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthorized = await client.get("/analytics/dashboard")
            main.app.dependency_overrides[get_db] = override_db
            main.app.dependency_overrides[get_current_user] = override_user
            authorized = await client.get(
                "/analytics/dashboard?from=2026-09-01&to=2026-09-30",
                headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
            )
        assert unauthorized.status_code == 401
        assert authorized.status_code == 200
        assert observed["organization_id"] == user.organization_id
        assert authorized.json()["summary"]["revenue_minor"] == 0
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(get_current_user, None)
