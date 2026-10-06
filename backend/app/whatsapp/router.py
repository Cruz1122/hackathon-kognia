from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.settings import settings
from ..auth.dependencies import get_current_user
from ..db.models import ChannelBinding, Conversation, Customer, User
from ..db.session import get_db
from .service import receive

router = APIRouter()


@router.get('/webhooks/whatsapp')
async def challenge(request: Request):
    config = settings()
    if not config.whatsapp_enabled:
        raise HTTPException(503, 'WhatsApp is not configured')
    query = request.query_params
    if query.get('hub.mode') != 'subscribe' or not hmac.compare_digest(query.get('hub.verify_token', ''), config.whatsapp_verify):
        raise HTTPException(403, 'Invalid verification token')
    return PlainTextResponse(query.get('hub.challenge', ''))


def verify_signature(raw: bytes, signature: str, secret: str) -> bool:
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(signature, expected)


@router.post('/webhooks/whatsapp')
async def webhook(request: Request):
    config = settings()
    if not config.whatsapp_enabled:
        raise HTTPException(503, 'WhatsApp is not configured')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1024 * 1024:
            raise HTTPException(413, 'Payload too large')
    if not verify_signature(bytes(raw), request.headers.get('x-hub-signature-256', ''), config.whatsapp_secret):
        raise HTTPException(403, 'Invalid signature')
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Invalid envelope')
        await receive(payload)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise HTTPException(400, 'Invalid WhatsApp payload') from exc
    return {'ok': True}


class BindingRequest(BaseModel):
    customer_id: uuid.UUID | None = None
    wa_id: str = Field(pattern=r'^\d{7,15}$')
    phone: str = Field(pattern=r'^\+[1-9]\d{6,14}$')
    identity_verified: bool
    opt_in: bool = False
    template_name: str | None = Field(default=None, pattern=r'^[a-z0-9_]{1,120}$')
    template_language: str = Field(default='es', pattern=r'^[a-z]{2,3}(?:_[A-Z]{2})?$')


@router.put('/conversations/{conversation_id}/whatsapp')
async def bind(conversation_id: uuid.UUID, body: BindingRequest,
               user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.organization_id:
        raise HTTPException(403, 'A tenant administrator is required')
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
        Conversation.organization_id == user.organization_id).with_for_update())
    if not conversation:
        raise HTTPException(404, 'Conversation not found')
    customer_id = conversation.customer_id or body.customer_id
    if body.customer_id and conversation.customer_id and body.customer_id != conversation.customer_id:
        raise HTTPException(409, 'Conversation already belongs to another customer')
    customer = await db.get(Customer, customer_id) if customer_id else None
    if (not body.identity_verified or not customer or customer.organization_id != user.organization_id
            or customer.phone != body.phone or body.wa_id != body.phone.removeprefix('+')):
        raise HTTPException(422, 'Verify and link the conversation customer phone first')
    conversation.customer_id = customer.id
    number = settings().whatsapp_number
    if not number:
        raise HTTPException(503, 'WhatsApp number is not configured')
    binding = await db.scalar(select(ChannelBinding).where(ChannelBinding.organization_id == user.organization_id,
        ChannelBinding.phone == body.phone))
    if binding and binding.conversation_id != conversation_id:
        raise HTTPException(409, 'Identity is already linked to a different conversation')
    if binding is None:
        binding = ChannelBinding(conversation_id=conversation_id, organization_id=user.organization_id,
            phone_number_id=number, wa_id=body.wa_id, phone=body.phone)
        db.add(binding)
    binding.opt_in = body.opt_in
    binding.template_name = body.template_name
    binding.template_language = body.template_language
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Identity is already linked') from exc
    return {'id': str(binding.id), 'conversation_id': str(conversation_id)}
