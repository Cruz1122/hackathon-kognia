from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_analytics_cache_ttl_seconds
from ..db.models import Organization
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
        try:
            value = await redis_get(key)
            if value is None:
                return None
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    async def set(self, key: str, payload: dict[str, Any]) -> bool:
        try:
            return await redis_set(key, json.dumps(payload, default=str, separators=(",", ":")), ttl=get_analytics_cache_ttl_seconds())
        except Exception:
            return False

    async def invalidate(self, organization_id: uuid.UUID) -> bool:
        """Keep the pre-revision Redis invalidation API for older integrations."""
        try:
            await redis_incr(version_key(organization_id))
            return True
        except Exception:
            return False


async def bump_version(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        update(Organization)
        .where(Organization.id == organization_id)
        .values(analytics_version=Organization.analytics_version + 1)
    )


__all__ = ["AnalyticsCache", "bump_version", "dashboard_key", "version_key"]
