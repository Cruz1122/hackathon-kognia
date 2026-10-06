from __future__ import annotations

import logging
import uuid

from .recorder import TraceRecorder

logger = logging.getLogger("hackathon.tracing")


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def save_trace(recorder: TraceRecorder | None) -> None:
    """Persist one turn trace best-effort; never raise into the call path."""
    if recorder is None or not recorder.spans:
        return
    organization_id = _as_uuid(recorder.organization_id)
    conversation_id = _as_uuid(recorder.conversation_id)
    if organization_id is None or conversation_id is None:
        return
    try:
        from ...db.models import AgentTrace
        from ...db.session import get_session_factory

        async with get_session_factory()() as session:
            session.add(
                AgentTrace(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    call_id=_as_uuid(recorder.call_id),
                    provider=recorder.provider,
                    model=recorder.model,
                    status=recorder.status,
                    started_at=recorder.started_at,
                    duration_ms=recorder.duration_ms,
                    data=recorder.to_dict(),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Agent trace persistence failed")
