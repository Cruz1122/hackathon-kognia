from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from .sessions import CallSession

logger = logging.getLogger("hackathon.telnyx.timeline")


class CallTimelineService:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._task: asyncio.Task[None] | None = None

    def record(
        self,
        session: CallSession,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        provider_occurred_at: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        event = {
            "call_id": str(session.call_id),
            "organization_id": str(session.organization_id) if session.organization_id else None,
            "seq": session.next_sequence(),
            "type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "offset_ms": session.offset_ms(),
            "provider_occurred_at": provider_occurred_at,
            "payload": payload or {},
        }
        if event_type != "transcript.partial":
            session.events.append(event)
        if persist and session.organization_id is not None and event_type != "transcript.partial":
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Timeline queue full; audio continues")
        self._fanout(session, event)
        return event

    def _fanout(self, session: CallSession, event: dict[str, Any]) -> None:
        if session.organization_id is None:
            return
        from .live_audio import monitor_hub

        monitor_hub.publish(
            session.organization_id,
            session.call_id,
            {
                "type": event["type"],
                "seq": event["seq"],
                "offset_ms": event["offset_ms"],
                "payload": event["payload"],
            },
        )

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self._persist(event)
            except Exception:
                logger.exception("Timeline persist failed")
            finally:
                self.queue.task_done()

    async def _persist(self, event: dict[str, Any]) -> None:
        import uuid

        from sqlalchemy import update

        from ..db.models import Call, CallEvent, TranscriptSegment
        from ..db.session import get_session_factory

        organization_id = event.get("organization_id")
        if not organization_id:
            return
        occurred_at = datetime.fromisoformat(event["occurred_at"])
        provider_at = (
            datetime.fromisoformat(event["provider_occurred_at"]) if event.get("provider_occurred_at") else None
        )
        async with get_session_factory()() as session:
            session.add(
                CallEvent(
                    call_id=uuid.UUID(event["call_id"]),
                    organization_id=uuid.UUID(organization_id),
                    seq=int(event["seq"]),
                    event_type=event["type"],
                    occurred_at=occurred_at,
                    offset_ms=int(event["offset_ms"]),
                    provider_occurred_at=provider_at,
                    payload=event["payload"],
                )
            )
            if event["type"] == "transcript.final":
                payload = event["payload"] or {}
                session.add(
                    TranscriptSegment(
                        call_id=uuid.UUID(event["call_id"]),
                        organization_id=uuid.UUID(organization_id),
                        seq=int(event["seq"]),
                        speaker=str(payload.get("speaker") or "customer"),
                        text=str(payload.get("text") or ""),
                        offset_ms=int(event["offset_ms"]),
                        is_final=True,
                    )
                )
            values: dict[str, Any] = {"next_sequence": int(event["seq"])}
            if event["type"] == "lifecycle":
                values["lifecycle_state"] = str((event["payload"] or {}).get("state") or "ACTIVE")
            await session.execute(
                update(Call).where(Call.id == uuid.UUID(event["call_id"])).values(**values)
            )
            await session.commit()


timeline = CallTimelineService()
