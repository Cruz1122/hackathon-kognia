"""Dev-mode read API: per-call model traces for the authenticated tenant."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user
from ...db.models import AgentTrace, Call, Conversation, User
from ...db.session import get_db
from ...platform.pricing import estimate_cost_usd, pricing_source, refresh_prices

router = APIRouter(tags=["dev"])


async def _ensure_prices() -> None:
    """Best-effort: load OpenRouter prices (cached); fallback stays in pricing.py."""
    try:
        await refresh_prices()
    except Exception:
        pass


def _tenant(user: User) -> uuid.UUID:
    if user.organization_id is None:
        raise HTTPException(
            status_code=403,
            detail="An organization is required for this operation.",
        )
    return user.organization_id


def _preview(data: dict[str, Any] | None) -> str:
    if not isinstance(data, dict):
        return ""
    prompt = str(data.get("prompt") or "").strip()
    return prompt[:140]


def _usage(data: dict[str, Any] | None) -> dict[str, int]:
    raw = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
        "llm_calls": int(raw.get("llm_calls") or 0),
    }


def _sum_usage(rows: list[AgentTrace]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0}
    for row in rows:
        usage = _usage(row.data if isinstance(row.data, dict) else None)
        for key in totals:
            totals[key] += usage[key]
    return totals


def _cost_usd(data: dict[str, Any] | None, fallback_model: str | None) -> float | None:
    """Sum per-request cost using each span's model; fall back to the row model."""
    cost = 0.0
    known = False
    spans = data.get("spans") if isinstance(data, dict) else None
    if isinstance(spans, list):
        for span in spans:
            if not isinstance(span, dict) or span.get("name") != "llm.request":
                continue
            attributes = span.get("attributes")
            if not isinstance(attributes, dict):
                continue
            span_cost = estimate_cost_usd(
                attributes.get("model"),
                int(attributes.get("prompt_tokens") or 0),
                int(attributes.get("completion_tokens") or 0),
            )
            if span_cost is not None:
                cost += span_cost
                known = True
    if known:
        return round(cost, 6)
    usage = _usage(data)
    return estimate_cost_usd(fallback_model, usage["prompt_tokens"], usage["completion_tokens"])


def _call_summary(rows: list[AgentTrace]) -> dict[str, Any]:
    usage = _sum_usage(rows)
    duration = sum(row.duration_ms for row in rows)
    status = "ok" if all(row.status == "ok" for row in rows) else "error"
    cost = 0.0
    cost_known = False
    for row in rows:
        row_cost = _cost_usd(row.data if isinstance(row.data, dict) else None, row.model)
        if row_cost is not None:
            cost += row_cost
            cost_known = True
    return {
        **usage,
        "turns": len(rows),
        "duration_ms": duration,
        "status": status,
        "cost_usd": round(cost, 6) if cost_known else None,
    }


def _turn_view(row: AgentTrace) -> dict[str, Any]:
    data = row.data if isinstance(row.data, dict) else {}
    return {
        "id": str(row.id),
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "duration_ms": row.duration_ms,
        "prompt": data.get("prompt") or "",
        "answer": data.get("answer") or "",
        "usage": _usage(data),
        "cost_usd": _cost_usd(data, row.model),
        "data": data,
    }


@router.get("/dev/calls")
async def list_dev_calls(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    organization_id = _tenant(user)
    await _ensure_prices()
    rows = list(
        (
            await session.scalars(
                select(AgentTrace)
                .where(AgentTrace.organization_id == organization_id)
                .order_by(AgentTrace.started_at.desc())
                .limit(500)
            )
        ).all()
    )
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.call_id) if row.call_id else f"conv-{row.conversation_id}"
        group = groups.get(key)
        if group is None:
            group = {
                "key": key,
                "call_id": str(row.call_id) if row.call_id else None,
                "conversation_id": str(row.conversation_id),
                "turns": 0,
                "started_at": row.started_at.isoformat(),
                "updated_at": row.started_at.isoformat(),
                "provider": row.provider,
                "model": row.model,
                "status": "ok",
                "preview": _preview(row.data),
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
            groups[key] = group
        usage = _usage(row.data if isinstance(row.data, dict) else None)
        group["total_tokens"] += usage["total_tokens"]
        group["prompt_tokens"] += usage["prompt_tokens"]
        group["completion_tokens"] += usage["completion_tokens"]
        row_cost = _cost_usd(row.data if isinstance(row.data, dict) else None, row.model)
        if row_cost is not None:
            group["cost_usd"] = round(float(group["cost_usd"]) + row_cost, 6)
        group["turns"] += 1
        started = row.started_at.isoformat()
        if started < group["started_at"]:
            group["started_at"] = started
        if started > group["updated_at"]:
            group["updated_at"] = started
        if row.provider:
            group["provider"] = row.provider
        if row.model:
            group["model"] = row.model
        if row.status != "ok":
            group["status"] = row.status

    conversation_ids = {uuid.UUID(item["conversation_id"]) for item in groups.values()}
    if conversation_ids:
        conversations = {
            conversation.id: conversation
            for conversation in (
                await session.scalars(
                    select(Conversation).where(
                        Conversation.organization_id == organization_id,
                        Conversation.id.in_(conversation_ids),
                    )
                )
            ).all()
        }
        for item in groups.values():
            conversation = conversations.get(uuid.UUID(item["conversation_id"]))
            item["channel"] = conversation.channel if conversation else None

    calls = sorted(groups.values(), key=lambda item: item["updated_at"], reverse=True)
    return {"calls": calls}


async def _turn_rows(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    call_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> list[AgentTrace]:
    query = select(AgentTrace).where(AgentTrace.organization_id == organization_id)
    if call_id is not None:
        query = query.where(AgentTrace.call_id == call_id)
    elif conversation_id is not None:
        query = query.where(AgentTrace.conversation_id == conversation_id)
    else:
        return []
    query = query.order_by(AgentTrace.started_at)
    return list((await session.scalars(query)).all())


@router.get("/calls/{call_id}/traces")
async def get_call_traces(
    call_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    organization_id = _tenant(user)
    await _ensure_prices()
    call = await session.get(Call, call_id)
    if call is None or call.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="not found")
    rows = await _turn_rows(
        session,
        organization_id=organization_id,
        call_id=call_id,
        conversation_id=None,
    )
    return {
        "call_id": str(call_id),
        "conversation_id": str(call.conversation_id),
        "pricing_source": pricing_source(),
        "summary": _call_summary(rows),
        "turns": [_turn_view(row) for row in rows],
    }


@router.get("/dev/conversations/{conversation_id}/traces")
async def get_conversation_traces(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    organization_id = _tenant(user)
    await _ensure_prices()
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="not found")
    rows = await _turn_rows(
        session,
        organization_id=organization_id,
        call_id=None,
        conversation_id=conversation_id,
    )
    return {
        "call_id": None,
        "conversation_id": str(conversation_id),
        "pricing_source": pricing_source(),
        "summary": _call_summary(rows),
        "turns": [_turn_view(row) for row in rows],
    }
