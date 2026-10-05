from pydantic import BaseModel

from ..agent.tools.contracts import ToolContext, ToolDefinition


class CallbackArgs(BaseModel):
    """No user input: the destination is the conversation's verified identity."""


async def call_customer(args: CallbackArgs, context: ToolContext):
    import base64
    import uuid
    from ..db.session import get_session_factory
    from ..telephony.settings import load_settings, telnyx_enabled
    from ..telephony.telnyx_api import TelnyxApi

    if not context.operation_id or not context.organization_id or not context.conversation_id:
        raise ValueError('Authorized operation and conversation are required')
    if not telnyx_enabled():
        raise ValueError('Telnyx is not configured')
    async with get_session_factory()() as db:
        # The callback always goes to the verified channel identity the customer used.
        from .identity import verified_destination
        binding = await verified_destination(db, uuid.UUID(context.organization_id), uuid.UUID(context.conversation_id))
        if not binding or not binding.phone:
            raise ValueError('No verified conversation identity to call')
    data = await TelnyxApi(load_settings()).dial(binding.phone,
        command_id=context.operation_id, client_state=base64.b64encode(context.operation_id.encode()).decode())
    return {'status': 'dialing', 'phone': binding.phone,
            **{key: data[key] for key in ('call_control_id', 'call_leg_id', 'call_session_id')}}


def register_tools(registry):
    registry.register(ToolDefinition('call_customer',
        "Request a real PSTN callback to the customer's verified phone. The number is taken automatically "
        "from the conversation, so never ask for it. Call this tool immediately when the customer asks to be "
        "called back: it registers the confirmation proposal. Do not ask for confirmation in words before calling it.",
        CallbackArgs, call_customer, 'write', timeout_s=15))
