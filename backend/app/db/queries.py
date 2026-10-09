from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics.cache import bump_version
from .models import Conversation, Message, User, UserRole


async def create_conversation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    channel: str,
    status: str,
) -> Conversation:
    """Create a conversation after validating the creator's tenant scope."""
    creator = await session.scalar(select(User).where(User.id == created_by))
    if creator is None:
        raise ValueError("Conversation creator was not found.")
    if creator.role != UserRole.SUPERADMIN and creator.organization_id != organization_id:
        raise ValueError("Conversation creator does not belong to the organization.")

    conversation = Conversation(
        organization_id=organization_id,
        created_by=created_by,
        channel=channel,
        status=status,
    )
    session.add(conversation)
    await bump_version(session, organization_id)
    await session.flush()
    return conversation


async def get_conversation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Conversation | None:
    """Load one conversation only when it belongs to the requested organization."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def list_messages(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[Message]:
    """Load conversation messages through a tenant-scoped conversation join."""
    result = await session.execute(
        select(Message)
        .join(Message.conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
        )
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars().all())


__all__ = ["create_conversation", "get_conversation", "list_messages"]
