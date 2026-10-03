from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class CallSession:
    call_id: uuid.UUID
    token: str
    telnyx_call_control_id: str
    call_leg_id: str | None = None
    call_session_id: str | None = None
    organization_id: uuid.UUID | None = None
    system_user_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    caller: str = ""
    callee: str = ""
    started_at: datetime = field(default_factory=_utcnow)
    monotonic_zero: float = field(default_factory=time.monotonic)
    sequence: int = 0
    last_offset_ms: int = 0
    recording_offset_ms: int = 0
    lifecycle_state: str = "RINGING"
    agent_state: str = "listening"
    media_format: dict[str, Any] = field(default_factory=dict)
    customer_pcm: bytearray = field(default_factory=bytearray)
    agent_pcm: bytearray = field(default_factory=bytearray)
    history: list[dict[str, str]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    websocket: Any = None
    stt: Any = None
    turn_task: Any = None
    playback_cancel: Any = None
    marks: Any = None
    closed: bool = False
    answered: bool = False
    token_expires_at: float = 0.0
    last_voice_at: float = 0.0
    first_voice_at: float = 0.0
    utterance_offset_ms: int | None = None
    agent_segment_open: bool = False
    playback_spans: list[tuple[int, int, str]] = field(default_factory=list)
    turn_started_at: float = 0.0
    barge_hits: int = 0

    def offset_ms(self) -> int:
        offset = int((time.monotonic() - self.monotonic_zero) * 1000)
        if offset < self.last_offset_ms:
            offset = self.last_offset_ms
        self.last_offset_ms = offset
        return offset

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def public_view(self) -> dict[str, Any]:
        return {
            "id": str(self.call_id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "caller": self.caller,
            "callee": self.callee,
            "lifecycle": self.lifecycle_state,
            "agent_state": self.agent_state,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.last_offset_ms,
            "recording_offset_ms": self.recording_offset_ms,
        }


class CallRegistry:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, CallSession] = {}
        self._by_control: dict[str, uuid.UUID] = {}
        self._events: dict[str, float] = {}

    def claim_event(self, event_id: str) -> bool:
        now = time.monotonic()
        expired = [key for key, seen_at in self._events.items() if now - seen_at > 3600]
        for key in expired:
            self._events.pop(key, None)
        if event_id in self._events:
            return False
        self._events[event_id] = now
        return True

    def release_event(self, event_id: str) -> None:
        self._events.pop(event_id, None)

    def create(
        self,
        *,
        telnyx_call_control_id: str,
        call_leg_id: str | None,
        call_session_id: str | None,
        caller: str,
        callee: str,
    ) -> CallSession:
        existing = self.by_control(telnyx_call_control_id)
        if existing is not None and not existing.closed:
            return existing
        session = CallSession(
            call_id=uuid.uuid4(),
            token=secrets.token_urlsafe(32),
            telnyx_call_control_id=telnyx_call_control_id,
            call_leg_id=call_leg_id,
            call_session_id=call_session_id,
            caller=caller,
            callee=callee,
            token_expires_at=time.monotonic() + 7200,
        )
        self._by_id[session.call_id] = session
        self._by_control[telnyx_call_control_id] = session.call_id
        return session

    def get(self, call_id: uuid.UUID) -> CallSession | None:
        return self._by_id.get(call_id)

    def by_control(self, call_control_id: str) -> CallSession | None:
        call_id = self._by_control.get(call_control_id)
        if call_id is None:
            return None
        return self._by_id.get(call_id)

    def token_matches(self, session: CallSession, token: str) -> bool:
        import hmac

        if session.closed and time.monotonic() > session.token_expires_at:
            return False
        if time.monotonic() > session.token_expires_at:
            return False
        if len(token) != len(session.token):
            return False
        return hmac.compare_digest(token, session.token)

    def list_for(self, organization_id: uuid.UUID) -> list[CallSession]:
        return [
            session
            for session in self._by_id.values()
            if session.organization_id == organization_id and not session.closed
        ]

    def end(self, session: CallSession) -> None:
        session.lifecycle_state = "ENDED"
        session.closed = True
        session.token_expires_at = time.monotonic() + 60


registry = CallRegistry()
