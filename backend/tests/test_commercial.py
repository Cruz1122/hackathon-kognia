from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.commercial.service import mark_won, start_recovery
from app.db.models import Opportunity, OpportunityStatus, RecoveryChannel


@pytest.mark.asyncio
async def test_recovery_and_won_update_one_opportunity() -> None:
    organization_id = uuid.uuid4()
    opportunity = Opportunity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        conversation_id=uuid.uuid4(),
        status=OpportunityStatus.LOST,
        amount_minor=400,
        lost_at=datetime(2026, 9, 16, tzinfo=UTC),
        lost_reason="price",
    )
    session = AsyncMock()
    session.scalar.return_value = opportunity
    started = datetime(2026, 9, 17, tzinfo=UTC)
    won = datetime(2026, 9, 18, tzinfo=UTC)

    await start_recovery(session, organization_id=organization_id, opportunity_id=opportunity.id, at=started)
    await mark_won(session, organization_id=organization_id, opportunity_id=opportunity.id, at=won)
    assert opportunity.recovery_channel == RecoveryChannel.WHATSAPP
    assert opportunity.recovery_started_at == started
    assert opportunity.recovered_at == won
    assert opportunity.status == OpportunityStatus.WON
    assert opportunity.won_at == won
    assert opportunity.lost_at is None
    assert opportunity.lost_reason is None
    assert session.commit.await_count == 2
    assert session.execute.await_count == 2
