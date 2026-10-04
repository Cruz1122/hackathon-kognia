from pydantic import BaseModel, Field

from ..agent.tools.contracts import ToolContext, ToolDefinition


class CallbackArgs(BaseModel):
    phone: str = Field(pattern=r'^\+[1-9]\d{6,14}$')


async def call_customer(args: CallbackArgs, context: ToolContext):
    import base64
    import uuid
    from sqlalchemy import select
    from ..db.models import ChannelBinding
    from ..db.session import get_session_factory
    from ..telephony.settings import load_settings, telnyx_enabled
    from ..telephony.telnyx_api import TelnyxApi

    if not context.operation_id or not telnyx_enabled():
        raise ValueError('Authorized operation and configured Telnyx are required')
    async with get_session_factory()() as db:
        binding = await db.scalar(select(ChannelBinding).where(
            ChannelBinding.organization_id == uuid.UUID(context.organization_id),
            ChannelBinding.conversation_id == uuid.UUID(context.conversation_id),
            ChannelBinding.phone == args.phone))
        if not binding:
            raise ValueError('Destination is not a verified conversation identity')
    data = await TelnyxApi(load_settings()).dial(args.phone,
        command_id=context.operation_id, client_state=base64.b64encode(context.operation_id.encode()).decode())
    return {'status': 'dialing', **{key: data[key] for key in ('call_control_id', 'call_leg_id', 'call_session_id')}}


def register_tools(registry):
    registry.register(ToolDefinition('call_customer',
        'Request a real PSTN callback to the verified customer phone. Requires presenting the phone and explicit confirmation.',
        CallbackArgs, call_customer, 'write', timeout_s=15))
