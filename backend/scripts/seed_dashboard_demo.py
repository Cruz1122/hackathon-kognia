"""Seed deterministic commercial data for the dashboard demo.

Usage: python -m scripts.seed_dashboard_demo [--count 300]
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select, update

from app.db.models import (
    Call,
    CallStatus,
    Conversation,
    Customer,
    Message,
    MessageRole,
    Objection,
    Opportunity,
    OpportunityStatus,
    Organization,
    Product,
    ProductInterest,
    User,
)
from app.db.session import dispose_engine, get_session_factory


PRODUCTS = ("Kognia Pro", "Kognia Teams", "Kognia Enterprise", "Kognia Insights")
OBJECTIONS = ("precio", "implementación", "competencia", "funcionalidad", "tiempo")
LOST_REASONS = ("precio", "sin presupuesto", "competencia", "sin respuesta", "timing")
FIRST_NAMES = ("Laura", "Andrés", "Camila", "Felipe", "Valentina", "Santiago", "Mariana", "Nicolás")
LAST_NAMES = ("Gómez", "Rodríguez", "Martínez", "López", "Torres", "Ramírez", "Castro", "Vargas")


async def seed(count: int, slug: str) -> int:
    rng = random.Random(20260917)
    async with get_session_factory()() as session:
        organization = await session.scalar(select(Organization).where(Organization.slug == slug))
        if organization is None:
            raise RuntimeError(f"Organization '{slug}' does not exist; run the auth bootstrap first.")
        creator = await session.scalar(select(User).where(User.organization_id == organization.id).order_by(User.created_at))
        if creator is None:
            raise RuntimeError("The organization has no admin user.")

        existing = int((await session.scalar(select(func.count(Call.id)).where(Call.organization_id == organization.id, Call.external_id.like("synthetic-dashboard-%"))) or 0))
        if existing >= count:
            print(f"Dashboard demo data already has {existing} synthetic conversations.")
            return existing

        product_rows = list((await session.scalars(select(Product).where(Product.organization_id == organization.id))).all())
        by_name = {product.name: product for product in product_rows}
        for name in PRODUCTS:
            if name not in by_name:
                product = Product(id=uuid.uuid4(), organization_id=organization.id, name=name)
                session.add(product)
                by_name[name] = product
        await session.flush()

        now = datetime.now(UTC).replace(hour=17, minute=0, second=0, microsecond=0)
        rows_to_add = []
        for index in range(existing, count):
            created_at = now - timedelta(days=rng.randint(0, 44), hours=rng.randint(0, 8), minutes=rng.randint(0, 59))
            conversation_id = uuid.uuid4()
            customer_id = uuid.uuid4()
            call_id = uuid.uuid4()
            opportunity_id = uuid.uuid4()
            customer = Customer(id=customer_id, organization_id=organization.id, name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", phone=f"+57 3{rng.randint(10, 99)} {rng.randint(1000000, 9999999)}", created_at=created_at, updated_at=created_at)
            conversation = Conversation(id=conversation_id, organization_id=organization.id, customer_id=customer_id, created_by=creator.id, channel=rng.choices(("voice", "whatsapp"), weights=(7, 3))[0], status="completed", created_at=created_at, updated_at=created_at + timedelta(minutes=rng.randint(3, 18)))
            duration = timedelta(minutes=rng.randint(3, 16), seconds=rng.randint(0, 59))
            call = Call(id=call_id, organization_id=organization.id, conversation_id=conversation_id, started_at=created_at, ended_at=created_at + duration, status=CallStatus.ENDED, external_id=f"synthetic-dashboard-{index:04d}", created_at=created_at)
            product = rng.choice(tuple(by_name.values()))
            status = rng.choices((OpportunityStatus.WON, OpportunityStatus.LOST, OpportunityStatus.PENDING), weights=(35, 42, 23))[0]
            amount = rng.randrange(900, 8500) * 1000
            won_at = created_at + timedelta(hours=rng.randint(1, 48)) if status == OpportunityStatus.WON else None
            lost_at = created_at + timedelta(hours=rng.randint(1, 48)) if status == OpportunityStatus.LOST else None
            recovery_started = created_at + timedelta(days=rng.randint(2, 10)) if status == OpportunityStatus.WON and rng.random() < .18 else None
            recovered_at = recovery_started + timedelta(hours=rng.randint(4, 72)) if recovery_started else None
            opportunity = Opportunity(id=opportunity_id, organization_id=organization.id, conversation_id=conversation_id, customer_id=customer_id, product_id=product.id, status=status, amount_minor=amount, currency="COP", lost_reason=rng.choice(LOST_REASONS) if status == OpportunityStatus.LOST else None, recovery_started_at=recovery_started, recovered_at=recovered_at, recovery_channel="whatsapp" if recovery_started else None, won_at=won_at, lost_at=lost_at, created_at=created_at, updated_at=created_at)
            user_text = rng.choice(("Busco mejorar el seguimiento comercial.", "Necesito entender por qué se pierden oportunidades.", "Quiero una demo para mi equipo."))
            assistant_text = rng.choice(("Te muestro cómo Kognia convierte cada conversación en una señal accionable.", "Podemos medir objeciones, resultados y recuperación en un mismo flujo."))
            message_one = Message(id=uuid.uuid4(), conversation_id=conversation_id, role=MessageRole.USER, content=user_text, channel=conversation.channel, created_at=created_at + timedelta(seconds=18))
            message_two = Message(id=uuid.uuid4(), conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=assistant_text, channel=conversation.channel, created_at=created_at + timedelta(seconds=54))
            rows_to_add.extend((customer, conversation, call, opportunity, message_one, message_two, ProductInterest(id=uuid.uuid4(), organization_id=organization.id, conversation_id=conversation_id, product_id=product.id, created_at=created_at + timedelta(minutes=1))))
            if rng.random() < .67:
                category = rng.choice(OBJECTIONS)
                resolved = rng.random() < (.72 if status == OpportunityStatus.WON else .46)
                rows_to_add.append(Objection(id=uuid.uuid4(), organization_id=organization.id, conversation_id=conversation_id, category=category, resolved=resolved, source="enrichment", created_at=created_at + timedelta(minutes=2), resolved_at=created_at + timedelta(minutes=4) if resolved else None))

        session.add_all(rows_to_add)
        await session.execute(update(Organization).where(Organization.id == organization.id).values(analytics_version=Organization.analytics_version + 1))
        await session.commit()
        total = existing + count - existing
        print(f"Seeded {count - existing} synthetic conversations; organization total is now at least {total} demo rows.")
        return total


async def main(count: int, slug: str) -> None:
    try:
        await seed(count, slug)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.cwd() / "backend" / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--slug", default="demo-kognia")
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be positive")
    asyncio.run(main(args.count, args.slug))
