from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..analytics.cache import bump_version
from ..db.models import Customer, Opportunity, OpportunityStatus, Product, User
from ..db.session import get_db
from .service import mark_won, start_recovery

router = APIRouter(tags=["commercial"])


class CustomerCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    active: bool = True


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str | None
    phone: str | None
    created_at: datetime
    updated_at: datetime


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    active: bool
    created_at: datetime


class OpportunityCreate(BaseModel):
    conversation_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="COP", min_length=3, max_length=3)


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: uuid.UUID | None
    product_id: uuid.UUID | None
    status: OpportunityStatus
    amount_minor: int | None
    currency: str
    lost_reason: str | None
    recovery_started_at: datetime | None
    recovered_at: datetime | None
    recovery_channel: str | None
    won_at: datetime | None
    lost_at: datetime | None


def _tenant(user: User) -> uuid.UUID:
    if user.organization_id is None:
        raise HTTPException(status_code=403, detail="An organization is required for this operation.")
    return user.organization_id


@router.post("/customers", response_model=CustomerResponse, status_code=201)
async def create_customer(payload: CustomerCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> CustomerResponse:
    customer = Customer(organization_id=_tenant(user), name=payload.name, phone=payload.phone)
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.post("/products", response_model=ProductResponse, status_code=201)
async def create_product(payload: ProductCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> ProductResponse:
    product = Product(organization_id=_tenant(user), name=payload.name, active=payload.active)
    session.add(product)
    try:
        await session.commit()
        await session.refresh(product)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Product could not be created.") from exc
    return ProductResponse.model_validate(product)


@router.post("/opportunities", response_model=OpportunityResponse, status_code=201)
async def create_opportunity(payload: OpportunityCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> Opportunity:
    opportunity = Opportunity(organization_id=_tenant(user), **payload.model_dump())
    session.add(opportunity)
    try:
        await bump_version(session, opportunity.organization_id)
        await session.commit()
        await session.refresh(opportunity)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Opportunity references are invalid.") from exc
    return opportunity


@router.post("/opportunities/{opportunity_id}/recovery", response_model=OpportunityResponse)
async def begin_opportunity_recovery(opportunity_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> Opportunity:
    opportunity = await start_recovery(session, organization_id=_tenant(user), opportunity_id=opportunity_id)
    return opportunity


@router.post("/opportunities/{opportunity_id}/won", response_model=OpportunityResponse)
async def win_opportunity(opportunity_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> Opportunity:
    opportunity = await mark_won(session, organization_id=_tenant(user), opportunity_id=opportunity_id)
    return opportunity
