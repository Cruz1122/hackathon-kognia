from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics.cache import bump_version
from ..db.models import Opportunity, OpportunityStatus, RecoveryChannel


async def start_recovery(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    at: datetime | None = None,
) -> Opportunity:
    opportunity = await _get_opportunity(session, organization_id, opportunity_id)
    if opportunity.status == OpportunityStatus.WON:
        raise HTTPException(status_code=409, detail="A won opportunity cannot start recovery.")
    opportunity.recovery_started_at = at or datetime.now(UTC)
    opportunity.recovery_channel = RecoveryChannel.WHATSAPP
    await bump_version(session, organization_id)
    await session.commit()
    return opportunity


async def mark_won(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    at: datetime | None = None,
) -> Opportunity:
    opportunity = await _get_opportunity(session, organization_id, opportunity_id)
    timestamp = at or datetime.now(UTC)
    opportunity.status = OpportunityStatus.WON
    opportunity.won_at = timestamp
    # Recovery can convert a previously lost opportunity. Clear the old
    # outcome fields so the status/timestamp invariants remain consistent.
    opportunity.lost_at = None
    opportunity.lost_reason = None
    if opportunity.recovery_started_at is not None and opportunity.recovered_at is None:
        opportunity.recovered_at = timestamp
    await bump_version(session, organization_id)
    await session.commit()
    return opportunity


async def _get_opportunity(session: AsyncSession, organization_id: uuid.UUID, opportunity_id: uuid.UUID) -> Opportunity:
    opportunity = await session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.organization_id == organization_id,
        )
    )
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    return opportunity
