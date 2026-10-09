from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Organization
# Kept as module attributes for compatibility with older tests/integrations.
# Production analytics deliberately uses PostgreSQL as its source of truth and
# does not perform Redis cache I/O.
from ..platform.redis import redis_get, redis_incr, redis_set


def version_key(organization_id: uuid.UUID) -> str:
    return f"analytics:version:{organization_id}"


def dashboard_key(organization_id: uuid.UUID, version: int, date_from: datetime, date_to: datetime) -> str:
    return f"analytics:{organization_id}:{version}:{date_from.isoformat()}:{date_to.isoformat()}"


class AnalyticsCache:
    async def version(self, session: AsyncSession, organization_id: uuid.UUID) -> int:
        value = await session.scalar(
            select(Organization.analytics_version).where(Organization.id == organization_id)
        )
        return int(value or 0)

    async def get(self, key: str) -> dict[str, Any] | None:
        del key
        return None

    async def set(self, key: str, payload: dict[str, Any]) -> bool:
        del key, payload
        return False

    async def invalidate(self, organization_id: uuid.UUID) -> bool:
        """Keep the invalidation API without introducing a Redis dependency."""
        del organization_id
        return True


async def bump_version(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        update(Organization)
        .where(Organization.id == organization_id)
        .values(analytics_version=Organization.analytics_version + 1)
    )


__all__ = ["AnalyticsCache", "bump_version", "dashboard_key", "version_key"]
