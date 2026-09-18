"""Run the deterministic commercial acceptance scenario against the local stack."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.auth.tokens import create_access_token  # noqa: E402
from app.commercial.service import mark_won, start_recovery  # noqa: E402
from app.db.models import (  # noqa: E402
    Call,
    Conversation,
    Message,
    MessageRole,
    Objection,
    Opportunity,
    OpportunityStatus,
    Organization,
    Product,
    ProductInterest,
    User,
    UserRole,
)
from app.db.session import dispose_engine, get_session_factory  # noqa: E402


def _at(day: int) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=UTC)


async def main() -> None:
    factory = get_session_factory()
    organization_ids: list[uuid.UUID] = []
    try:
        async with factory() as session:
            organization = Organization(name="E2E A", slug=f"e2e-a-{uuid.uuid4()}")
            other_organization = Organization(name="E2E B", slug=f"e2e-b-{uuid.uuid4()}")
            admin = User(
                organization=organization,
                email=f"e2e-a-{uuid.uuid4()}@test.invalid",
                password_hash="hash",
                role=UserRole.ADMIN,
            )
            other_admin = User(
                organization=other_organization,
                email=f"e2e-b-{uuid.uuid4()}@test.invalid",
                password_hash="hash",
                role=UserRole.ADMIN,
            )
            product = Product(organization=organization, name="E2E product")
            other_product = Product(organization=other_organization, name="Other product")
            session.add_all([organization, other_organization, admin, other_admin, product, other_product])
            await session.flush()
            organization_ids = [organization.id, other_organization.id]

            opportunities: list[Opportunity] = []
            for index in range(10):
                conversation = Conversation(
                    organization_id=organization.id,
                    created_by=admin.id,
                    channel="voice",
                    status="open",
                    created_at=_at(10),
                )
                session.add(conversation)
                await session.flush()
                initially_lost = index == 3 or index >= 4
                opportunity = Opportunity(
                    organization_id=organization.id,
                    conversation_id=conversation.id,
                    product_id=product.id,
                    status=OpportunityStatus.LOST if initially_lost else OpportunityStatus.WON,
                    amount_minor=[100, 200, 300, 400][index] if index < 4 else None,
                    lost_reason="price" if initially_lost else None,
                    lost_at=_at(10) if initially_lost else None,
                    won_at=_at(10) if not initially_lost else None,
                    created_at=_at(10),
                    updated_at=_at(10),
                )
                opportunities.append(opportunity)
                session.add_all(
                    [
                        Message(
                            conversation_id=conversation.id,
                            role=MessageRole.USER,
                            content="I need a product.",
                            channel="voice",
                            created_at=_at(10),
                        ),
                        opportunity,
                        ProductInterest(
                            organization_id=organization.id,
                            conversation_id=conversation.id,
                            product_id=product.id,
                            created_at=_at(10),
                        ),
                    ]
                )

            for index in range(5):
                session.add(
                    Objection(
                        organization_id=organization.id,
                        conversation_id=opportunities[index].conversation_id,
                        category="price",
                        resolved=index < 3,
                        created_at=_at(10),
                    )
                )
            other_conversation = Conversation(
                organization_id=other_organization.id,
                created_by=other_admin.id,
                channel="voice",
                status="open",
                created_at=_at(10),
            )
            session.add(other_conversation)
            await session.flush()
            session.add(
                Opportunity(
                    organization_id=other_organization.id,
                    conversation_id=other_conversation.id,
                    product_id=other_product.id,
                    status=OpportunityStatus.WON,
                    amount_minor=9999,
                    won_at=_at(10),
                    created_at=_at(10),
                    updated_at=_at(10),
                )
            )
            await session.commit()

            await start_recovery(session, organization_id=organization.id, opportunity_id=opportunities[3].id, at=_at(11))
            await start_recovery(session, organization_id=organization.id, opportunity_id=opportunities[4].id, at=_at(11))
            await mark_won(session, organization_id=organization.id, opportunity_id=opportunities[3].id, at=_at(12))

        async with httpx.AsyncClient(base_url="http://localhost:18474", timeout=10) as client:
            headers_a = {"Authorization": f"Bearer {create_access_token(admin.id)}"}
            headers_b = {"Authorization": f"Bearer {create_access_token(other_admin.id)}"}
            response_a = await client.get("/analytics/dashboard?from=2026-09-01&to=2026-10-01", headers=headers_a)
            response_b = await client.get("/analytics/dashboard?from=2026-09-01&to=2026-10-01", headers=headers_b)
            response_a.raise_for_status()
            response_b.raise_for_status()
            summary_a = response_a.json()["summary"]
            summary_b = response_b.json()["summary"]
            assert summary_a == {
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
            assert summary_b["opportunities"] == 1
            assert summary_b["revenue_minor"] == 9999
            assert summary_b != summary_a
            print("commercial E2E PASS", {"organization_a": summary_a, "organization_b": summary_b})
    finally:
        if organization_ids:
            async with factory() as session:
                conversation_ids = select(Conversation.id).where(Conversation.organization_id.in_(organization_ids))
                await session.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
                for model in (ProductInterest, Objection, Opportunity, Call, Conversation, Product, User):
                    await session.execute(delete(model).where(model.organization_id.in_(organization_ids)))
                await session.execute(delete(Organization).where(Organization.id.in_(organization_ids)))
                await session.commit()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
