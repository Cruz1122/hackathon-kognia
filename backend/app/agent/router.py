import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import AgentSnapshot, ChannelBinding, ChannelEvent, Conversation, User
from ..db.session import get_db
from .state import AgentState
from .store import conversation_state

router = APIRouter()


async def build_state_view(db: AsyncSession, conversation_id: uuid.UUID, organization_id: uuid.UUID) -> dict:
    """Operational memory plus the delivery outcome the plan requires, tenant-scoped."""
    row = await db.scalar(select(AgentSnapshot).where(AgentSnapshot.conversation_id == conversation_id,
        AgentSnapshot.organization_id == organization_id))
    view = dict(row.data) if row else AgentState(
        conversation_id=str(conversation_id), organization_id=str(organization_id)).model_dump(mode='json')
    binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.conversation_id == conversation_id,
        ChannelBinding.organization_id == organization_id))
    if binding:
        events = (await db.scalars(select(ChannelEvent).where(ChannelEvent.binding_id == binding.id,
            ChannelEvent.kind == 'outbound').order_by(ChannelEvent.created_at.desc()).limit(5))).all()
        view['delivery'] = {
            'binding': {'wa_id': binding.wa_id, 'opt_in': binding.opt_in,
                        'last_inbound_at': binding.last_inbound_at.isoformat() if binding.last_inbound_at else None},
            'recent_outbound': [{'status': event.status, 'external_id': event.external_id,
                                 'at': event.updated_at.isoformat()} for event in events],
        }
    view['blocked_reason'] = next((action.get('reason') for action in reversed(view.get('key_actions', []))
                                   if action.get('type') == 'response_blocked'), None)
    return view


@router.get('/conversations/{conversation_id}/agent-state')
async def state_view(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.organization_id is None:
        raise HTTPException(403, 'A tenant administrator is required')
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
        Conversation.organization_id == user.organization_id))
    if not conversation:
        raise HTTPException(404, 'Conversation not found')
    return await build_state_view(db, conversation_id, user.organization_id)


@router.post('/conversations/{conversation_id}/agent-resume')
async def resume(conversation_id: uuid.UUID, user: User = Depends(get_current_user)):
    if not user.organization_id:
        raise HTTPException(403, 'A tenant administrator is required')
    try:
        async with conversation_state(str(user.organization_id), str(conversation_id)) as store:
            store.state.handoff_requested = False
            store.state.authorized = None
            store.state.pending = None
            store.state.action('operator_resumed', user_id=str(user.id))
            await store.save()
    except ValueError as exc:
        raise HTTPException(404, 'Conversation not found') from exc
    return {'ok': True}
