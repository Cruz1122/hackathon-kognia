"""Dev-mode read API: per-call model traces for the authenticated tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...auth.dependencies import get_current_user
from ...db.models import AgentTrace, Call, CallEvent, Conversation, Message, Recording, User
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
    tools = 0
    rag_calls = 0
    for row in rows:
        data = row.data if isinstance(row.data, dict) else {}
        spans = data.get("spans")
        if not isinstance(spans, list):
            continue
        tools += sum(1 for span in spans if isinstance(span, dict) and str(span.get("name") or "").startswith("tool."))
        rag_calls += sum(1 for span in spans if isinstance(span, dict) and span.get("name") == "rag.retrieve")
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
        "tools": tools,
        "rag_calls": rag_calls,
        "duration_ms": duration,
        "status": status,
        "cost_usd": round(cost, 6) if cost_known else None,
    }


def _call_event_summary(rows: list[CallEvent]) -> dict[str, int]:
    """Recover observable tool/RAG counts for calls recorded before tracing."""
    return {
        "tools": sum(1 for row in rows if row.event_type == "tool.completed"),
        "rag_calls": sum(1 for row in rows if row.event_type == "rag.completed"),
    }


def _turn_view(row: AgentTrace, fallback_answer: str | None = None) -> dict[str, Any]:
    data = row.data if isinstance(row.data, dict) else {}
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        # Older traces can have an empty top-level answer even though the
        # observable model text was captured on the llm.request span.
        spans = data.get("spans")
        if isinstance(spans, list):
            for span in reversed(spans):
                if not isinstance(span, dict) or span.get("name") != "llm.request":
                    continue
                attributes = span.get("attributes")
                candidate = attributes.get("text") if isinstance(attributes, dict) else None
                if isinstance(candidate, str) and candidate.strip():
                    answer = candidate
                    break
    if (not isinstance(answer, str) or not answer.strip()) and isinstance(fallback_answer, str) and fallback_answer.strip():
        answer = fallback_answer
    if not isinstance(answer, str):
        answer = ""
    data_view = data
    if answer and (not isinstance(data.get("answer"), str) or not str(data.get("answer")).strip()):
        data_view = {**data, "answer": answer}
    return {
        "id": str(row.id),
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "duration_ms": row.duration_ms,
        "prompt": data.get("prompt") or "",
        "answer": answer,
        "usage": _usage(data),
        "cost_usd": _cost_usd(data, row.model),
        "data": data_view,
    }


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps before comparing historical rows."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _agent_event_turn_id(event: CallEvent, traces: list[AgentTrace]) -> str | None:
    """Associate an agent transcript with the closest trace that started before it."""
    if event.event_type != 'transcript.final' or not isinstance(event.payload, dict):
        return None
    if event.payload.get('speaker') != 'agent':
        return None
    occurred_at = _as_utc(event.occurred_at)
    candidates = [trace for trace in traces if _as_utc(trace.started_at) <= occurred_at]
    if not candidates:
        # A greeting or policy-only message can legitimately precede the first
        # persisted trace. Do not shift the first real turn onto that message.
        return None
    matched = max(candidates, key=lambda trace: _as_utc(trace.started_at))
    return str(matched.id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _stored_call_view(call: Call, recording: Recording | None) -> dict[str, Any]:
    conversation = call.conversation
    customer = conversation.customer if conversation is not None else None
    ended = call.ended_at
    duration_ms = int((ended - call.started_at).total_seconds() * 1000) if ended is not None else 0
    return {
        "id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "lifecycle": call.lifecycle_state,
        "status": str(call.status),
        "started_at": call.started_at.isoformat(),
        "ended_at": _iso(ended),
        "duration_ms": max(duration_ms, 0),
        "recording_duration_ms": max(0, int(recording.duration_ms or 0)) if recording else 0,
        "recording_offset_ms": max(0, int(call.recording_offset_ms or 0)),
        "caller": customer.phone if customer is not None and customer.phone else "",
        "customer_name": customer.name if customer is not None and customer.name else "",
    }


async def _recorded_calls(
    session: AsyncSession,
    organization_id: uuid.UUID,
    call_id: uuid.UUID | None = None,
) -> list[tuple[Call, Recording]]:
    query = (
        select(Call, Recording)
        .join(Recording, Recording.call_id == Call.id)
        .options(joinedload(Call.conversation).joinedload(Conversation.customer))
        .where(
            Call.organization_id == organization_id,
            Call.status.in_(('ended', 'failed')),
            Recording.organization_id == organization_id,
            Recording.status == 'READY',
        )
        .order_by(Call.started_at.desc())
    )
    if call_id is not None:
        query = query.where(Call.id == call_id)
    result = await session.execute(query)
    return [(call, recording) for call, recording in result.unique().all()]


def _span_kind(name: str) -> str:
    if name == 'llm.request':
        return 'llm'
    if name.startswith('tool.'):
        return 'tool'
    if name == 'rag.retrieve':
        return 'rag'
    if name == 'provider.error':
        return 'error'
    return 'trace'


def _span_title(name: str) -> str:
    if name.startswith('tool.'):
        return name[5:]
    return name


def _span_response(name: str, attributes: dict[str, Any]) -> dict[str, Any] | None:
    if name != 'llm.request':
        return None
    response = attributes.get('response')
    if isinstance(response, dict):
        return response
    # Historical traces predate the explicit response field. Reconstruct only
    # the observable application result; never expose provider envelopes.
    fallback: dict[str, Any] = {}
    if isinstance(attributes.get('text'), str):
        fallback['text'] = attributes['text']
    if isinstance(attributes.get('tool_calls'), list):
        fallback['tool_calls'] = attributes['tool_calls']
    usage = {
        key: int(attributes[key])
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
        if isinstance(attributes.get(key), (int, float))
    }
    if usage:
        fallback['usage'] = usage
    return fallback or None


def _event_playback_ms(offset_ms: int, recording_offset_ms: int, recording_duration_ms: int) -> int:
    relative = max(0, offset_ms - recording_offset_ms)
    if recording_duration_ms <= 0:
        return relative
    return min(relative, recording_duration_ms)


def _call_event_view(
    row: CallEvent,
    *,
    recording_offset_ms: int,
    recording_duration_ms: int,
    turn_id: str | None = None,
) -> dict[str, Any]:
    offset_ms = max(0, int(row.offset_ms or 0))
    return {
        "id": f"call-{row.seq}",
        "kind": "transcript" if row.event_type.startswith("transcript.") else row.event_type,
        "name": row.event_type,
        "offset_ms": offset_ms,
        "playback_ms": _event_playback_ms(offset_ms, recording_offset_ms, recording_duration_ms),
        "duration_ms": 0,
        "occurred_at": row.occurred_at.isoformat(),
        "turn_id": turn_id,
        "status": None,
        "usage": None,
        "payload": row.payload or {},
        "order": row.seq,
    }


def _trace_event_views(
    row: AgentTrace,
    *,
    call_started_at: datetime,
    recording_offset_ms: int,
    recording_duration_ms: int,
) -> list[dict[str, Any]]:
    data = row.data if isinstance(row.data, dict) else {}
    raw_spans = data.get('spans')
    if not isinstance(raw_spans, list):
        return []
    trace_start_ms = max(0, int((row.started_at - call_started_at).total_seconds() * 1000))
    views: list[dict[str, Any]] = []
    for index, span in enumerate(raw_spans):
        if not isinstance(span, dict):
            continue
        name = str(span.get('name') or 'trace')
        if name == 'agent.turn':
            continue
        attributes = span.get('attributes') if isinstance(span.get('attributes'), dict) else {}
        offset_ms = max(0, trace_start_ms + int(span.get('start_ms') or 0))
        duration_ms = max(0, int(span.get('duration_ms') or 0))
        usage = {
            key: int(attributes[key])
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            if isinstance(attributes.get(key), (int, float))
        }
        # ``response`` is exposed as its own normalized event field. Keep it
        # out of the span payload so the replay never conflates the model
        # result with the request/trace attributes.
        payload_attributes = {
            key: value
            for key, value in attributes.items()
            if key != 'response'
        }
        views.append({
            "id": f"trace-{row.id}-{index}",
            "kind": _span_kind(name),
            "name": _span_title(name),
            "span_name": name,
            "offset_ms": offset_ms,
            "playback_ms": _event_playback_ms(offset_ms, recording_offset_ms, recording_duration_ms),
            "duration_ms": duration_ms,
            "occurred_at": row.started_at.isoformat(),
            "turn_id": str(row.id),
            "status": attributes.get('status') or ('ok' if attributes.get('ok') is True else 'error' if attributes.get('ok') is False else None),
            "usage": usage or None,
            "response": _span_response(name, attributes),
            "payload": {
                "name": name,
                "attributes": payload_attributes,
            },
            "order": index,
        })
    return views


def _message_event_view(
    row: Message,
    *,
    call_started_at: datetime,
    recording_offset_ms: int,
    recording_duration_ms: int,
    order: int,
) -> dict[str, Any]:
    offset_ms = max(0, int((row.created_at - call_started_at).total_seconds() * 1000))
    return {
        "id": f"message-{row.id}",
        "kind": 'whatsapp' if row.channel == 'whatsapp' else 'system',
        "name": row.channel,
        "offset_ms": offset_ms,
        "playback_ms": _event_playback_ms(offset_ms, recording_offset_ms, recording_duration_ms),
        "duration_ms": 0,
        "occurred_at": row.created_at.isoformat(),
        "turn_id": None,
        "status": None,
        "usage": None,
        "payload": {
            "role": row.role.value if hasattr(row.role, 'value') else str(row.role),
            "content": row.content,
            "channel": row.channel,
        },
        "order": order,
    }


@router.get("/dev/calls")
async def list_dev_calls(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    organization_id = _tenant(user)
    await _ensure_prices()
    recorded = await _recorded_calls(session, organization_id)
    if not recorded:
        return {"calls": []}
    call_ids = [call.id for call, _recording in recorded]
    conversation_ids = [call.conversation_id for call, _recording in recorded]
    rows = list(
        (
            await session.scalars(
                select(AgentTrace)
                .where(
                    AgentTrace.organization_id == organization_id,
                    AgentTrace.call_id.in_(call_ids),
                )
                .order_by(AgentTrace.started_at)
            )
        ).all()
    )
    by_call: dict[uuid.UUID, list[AgentTrace]] = {}
    for row in rows:
        if row.call_id is not None:
            by_call.setdefault(row.call_id, []).append(row)
    call_events = list(
        (
            await session.scalars(
                select(CallEvent)
                .where(
                    CallEvent.organization_id == organization_id,
                    CallEvent.call_id.in_(call_ids),
                )
                .order_by(CallEvent.call_id, CallEvent.seq)
            )
        ).all()
    )
    events_by_call: dict[uuid.UUID, list[CallEvent]] = {}
    for event in call_events:
        events_by_call.setdefault(event.call_id, []).append(event)
    messages = list(
        (
            await session.scalars(
                select(Message)
                .join(Message.conversation)
                .where(
                    Message.conversation_id.in_(conversation_ids),
                    Message.channel.in_(('whatsapp', 'system')),
                    Conversation.organization_id == organization_id,
                )
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    continuation_by_conversation: dict[uuid.UUID, int] = {}
    for call, _recording in recorded:
        cutoff = call.ended_at or call.started_at
        continuation_by_conversation[call.conversation_id] = sum(
            1 for message in messages
            if message.conversation_id == call.conversation_id and message.created_at > cutoff
        )

    calls: list[dict[str, Any]] = []
    for call, recording in recorded:
        call_rows = by_call.get(call.id, [])
        summary = _call_summary(call_rows)
        first = call_rows[0] if call_rows else None
        preview = _preview(first.data if first is not None else None)
        recorded_events = events_by_call.get(call.id, [])
        event_summary = _call_event_summary(recorded_events)
        summary["tools"] = max(int(summary["tools"]), event_summary["tools"])
        summary["rag_calls"] = max(int(summary["rag_calls"]), event_summary["rag_calls"])
        calls.append({
            "key": str(call.id),
            "call_id": str(call.id),
            "conversation_id": str(call.conversation_id),
            "customer_name": _stored_call_view(call, recording)["customer_name"],
            "caller": _stored_call_view(call, recording)["caller"],
            "turns": summary["turns"],
            "started_at": call.started_at.isoformat(),
            "updated_at": call.ended_at.isoformat() if call.ended_at else call.started_at.isoformat(),
            "provider": first.provider if first else None,
            "model": first.model if first else None,
            "status": str(call.status),
            "preview": preview,
            "channel": call.conversation.channel if call.conversation is not None else 'voice',
            "duration_ms": _stored_call_view(call, recording)["duration_ms"],
            "recording_duration_ms": _stored_call_view(call, recording)["recording_duration_ms"],
            "total_tokens": summary["total_tokens"],
            "prompt_tokens": summary["prompt_tokens"],
            "completion_tokens": summary["completion_tokens"],
            "llm_calls": summary["llm_calls"],
            "tools": summary["tools"],
            "rag_calls": summary["rag_calls"],
            "cost_usd": summary["cost_usd"],
            "has_trace": bool(call_rows),
            "whatsapp_messages": continuation_by_conversation.get(call.conversation_id, 0),
            "event_count": len(recorded_events),
            "transcript_events": sum(1 for event in recorded_events if event.event_type == "transcript.final"),
        })
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


@router.get("/dev/calls/{call_id}/replay")
async def get_dev_call_replay(
    call_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return one recorded call as a replay-ready technical event stream."""
    organization_id = _tenant(user)
    await _ensure_prices()
    recorded = await _recorded_calls(session, organization_id, call_id)
    if not recorded:
        raise HTTPException(status_code=404, detail="Recorded call not found")
    call, recording = recorded[0]
    recording_offset_ms = max(0, int(call.recording_offset_ms or 0))
    recording_duration_ms = max(0, int(recording.duration_ms or 0))
    rows = await _turn_rows(
        session,
        organization_id=organization_id,
        call_id=call_id,
        conversation_id=None,
    )
    summary = _call_summary(rows)
    events: list[dict[str, Any]] = []
    call_events = list(
        (
            await session.scalars(
                select(CallEvent)
                .where(
                    CallEvent.call_id == call_id,
                    CallEvent.organization_id == organization_id,
                )
                .order_by(CallEvent.seq)
            )
        ).all()
    )
    summary = {
        **summary,
        "event_count": len(call_events),
        "transcript_events": sum(1 for event in call_events if event.event_type == "transcript.final"),
    }
    event_summary = _call_event_summary(call_events)
    summary["tools"] = max(int(summary["tools"]), event_summary["tools"])
    summary["rag_calls"] = max(int(summary["rag_calls"]), event_summary["rag_calls"])
    event_turn_ids = {
        event.id: _agent_event_turn_id(event, rows)
        for event in call_events
    }
    agent_answers_by_turn: dict[str, str] = {}
    for event in call_events:
        turn_id = event_turn_ids.get(event.id)
        text = str(event.payload.get("text") or "").strip() if isinstance(event.payload, dict) else ""
        if turn_id and text:
            agent_answers_by_turn[turn_id] = text
    events.extend(
        _call_event_view(
            row,
            recording_offset_ms=recording_offset_ms,
            recording_duration_ms=recording_duration_ms,
            turn_id=event_turn_ids.get(row.id),
        )
        for row in call_events
    )
    for row in rows:
        events.extend(
            _trace_event_views(
                row,
                call_started_at=call.started_at,
                recording_offset_ms=recording_offset_ms,
                recording_duration_ms=recording_duration_ms,
            )
        )
    messages = list(
        (
            await session.scalars(
                select(Message)
                .join(Message.conversation)
                .where(
                    Message.conversation_id == call.conversation_id,
                    Message.channel.in_(('whatsapp', 'system')),
                    Conversation.organization_id == organization_id,
                )
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    cutoff = call.ended_at or call.started_at
    messages = [message for message in messages if message.created_at > cutoff]
    events.extend(
        _message_event_view(
            row,
            call_started_at=call.started_at,
            recording_offset_ms=recording_offset_ms,
            recording_duration_ms=recording_duration_ms,
            order=index,
        )
        for index, row in enumerate(messages)
    )
    events.sort(key=lambda item: (int(item["playback_ms"]), int(item["offset_ms"]), int(item["order"])))
    for item in events:
        item.pop("order", None)
    return {
        "call": _stored_call_view(call, recording),
        "pricing_source": pricing_source(),
        "summary": summary,
        "turns": [_turn_view(row, agent_answers_by_turn.get(str(row.id))) for row in rows],
        "events": events,
        "has_trace": bool(rows),
        "whatsapp_messages": sum(1 for row in messages if row.channel == 'whatsapp'),
    }


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
