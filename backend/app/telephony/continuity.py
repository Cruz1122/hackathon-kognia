"""Resolve signed outbound events using server-owned durable operation IDs."""
import base64
import uuid

from sqlalchemy import select

from ..db.models import AgentOperation, Call, CallStatus, Conversation
from ..db.session import get_session_factory
from .sessions import registry


async def restore_outbound(body: dict):
    try:
        operation_id = base64.b64decode(body.get('client_state', ''), validate=True).decode('ascii')
        if len(operation_id) != 64 or any(char not in '0123456789abcdef' for char in operation_id):
            return None
    except (ValueError, UnicodeError):
        return None
    control = str(body.get('call_control_id') or '')
    if not control:
        return None
    async with get_session_factory()() as db:
        operation = await db.get(AgentOperation, operation_id)
        if not operation or operation.tool != 'call_customer':
            return None
        call_id = uuid.UUID(operation_id[:32])
        call = await db.get(Call, call_id)
        if call and call.status == CallStatus.ENDED:
            return None
        if call and call.telnyx_call_control_id != control:
            return None
        conversation = await db.scalar(select(Conversation).where(Conversation.id == operation.conversation_id,
            Conversation.organization_id == operation.organization_id))
        if not conversation:
            return None
        session = registry.create(telnyx_call_control_id=control,
            call_leg_id=body.get('call_leg_id'), call_session_id=body.get('call_session_id'),
            caller=str(body.get('from') or ''), callee=operation.arguments['phone'], call_id=call_id)
        session.organization_id = operation.organization_id
        session.conversation_id = operation.conversation_id
        session.system_user_id = conversation.created_by
        if call is None:
            db.add(Call(id=call_id, organization_id=operation.organization_id,
                conversation_id=operation.conversation_id, status=CallStatus.ACTIVE,
                telnyx_call_control_id=control, call_leg_id=session.call_leg_id,
                call_session_id=session.call_session_id, lifecycle_state='RINGING', external_id=control))
        # A signed callback proves that Telnyx accepted the dial, even if the
        # original HTTP response was lost or the waiting voice turn was cancelled.
        operation.status = 'succeeded'
        operation.result = {'status': 'dialing', 'call_control_id': control,
                            'call_leg_id': session.call_leg_id, 'call_session_id': session.call_session_id}
        await db.commit()
        return session
