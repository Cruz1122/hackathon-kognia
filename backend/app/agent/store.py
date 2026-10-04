from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSnapshot, Conversation
from ..db.session import get_engine
from .state import AgentState


@asynccontextmanager
async def advisory_lock(key: str):
    """Session lock survives short commits; always unlock before pooling."""
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    async with get_engine().connect() as connection:
        acquired = False
        try:
            async with asyncio.timeout(30):
                while not acquired:
                    acquired = bool(await connection.scalar(text('SELECT pg_try_advisory_lock(:id)'), {'id': lock_id}))
                    await connection.commit()
                    if not acquired:
                        await asyncio.sleep(0.1)
            yield connection
        finally:
            if acquired:
                try:
                    await connection.rollback()
                    await connection.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': lock_id})
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()
                    raise


@dataclass
class StateSession:
    db: AsyncSession
    row: AgentSnapshot
    state: AgentState

    async def save(self):
        self.state.version += 1
        self.row.data = self.state.model_dump(mode='json')
        await self.db.commit()


@asynccontextmanager
async def conversation_state(organization_id: str, conversation_id: str):
    org, cid = uuid.UUID(organization_id), uuid.UUID(conversation_id)
    async with advisory_lock(f'conversation:{org}:{cid}') as connection:
        async with AsyncSession(connection, expire_on_commit=False) as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.id == cid, Conversation.organization_id == org))
            if conversation is None:
                raise ValueError('Conversation not found')
            row = await db.get(AgentSnapshot, cid)
            state = AgentState.model_validate(row.data) if row else AgentState(
                organization_id=str(org), conversation_id=str(cid),
                customer_id=str(conversation.customer_id) if conversation.customer_id else None)
            if row is None:
                row = AgentSnapshot(conversation_id=cid, organization_id=org, data=state.model_dump(mode='json'))
                db.add(row)
            await db.commit()
            yield StateSession(db, row, state)


async def mark_presented(organization_id: str, conversation_id: str, proposal_id: str) -> None:
    async with conversation_state(organization_id, conversation_id) as store:
        pending = store.state.pending
        if pending and pending.fingerprint == proposal_id and not pending.presented:
            pending.presented = True
            store.state.action('proposal_presented', proposal=proposal_id)
            await store.save()
