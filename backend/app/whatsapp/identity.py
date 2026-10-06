"""Select a verified destination without selecting or inheriting its conversation."""
from sqlalchemy import select

from ..db.models import ChannelBinding, Conversation, Customer


async def verified_destination(db, organization_id, conversation_id):
    binding = await db.scalar(select(ChannelBinding).where(
        ChannelBinding.organization_id == organization_id,
        ChannelBinding.conversation_id == conversation_id))
    if binding is not None:
        return binding
    return await db.scalar(select(ChannelBinding).join(Customer,
        (Customer.organization_id == ChannelBinding.organization_id) & (Customer.phone == ChannelBinding.phone)
    ).join(Conversation, (Conversation.customer_id == Customer.id)
           & (Conversation.organization_id == Customer.organization_id)).where(
        Conversation.id == conversation_id, Conversation.organization_id == organization_id))
