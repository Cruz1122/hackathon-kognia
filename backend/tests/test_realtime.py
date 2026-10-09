from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.auth.tokens import create_access_token
from app.db.models import User, UserRole
from app.db.session import get_db
from app.realtime.events import RealtimeEvent
from app.realtime.hub import RealtimeHub


class FakeSocket:
    def __init__(self, *, fails: bool = False) -> None:
        self.messages: list[dict[str, object]] = []
        self.fails = fails

    async def send_json(self, message: dict[str, object]) -> None:
        if self.fails:
            raise RuntimeError("socket is closed")
        self.messages.append(message)


@pytest.mark.asyncio
async def test_hub_fans_out_to_multiple_subscribers_and_disconnects() -> None:
    hub = RealtimeHub()
    organization_id = uuid.uuid4()
    first = FakeSocket()
    second = FakeSocket()
    event = RealtimeEvent(
        type="agent.token",
        organization_id=organization_id,
        conversation_id=uuid.uuid4(),
        payload={"text": "Hola"},
    )

    hub.connect(first, organization_id)
    hub.connect(second, organization_id)
    assert await hub.publish(event) == 2
    assert first.messages == [event.model_dump(mode="json")]
    assert second.messages == [event.model_dump(mode="json")]

    hub.disconnect(first, organization_id)
    assert hub.connection_count(organization_id) == 1
    assert await hub.publish(event) == 1
    assert len(first.messages) == 1
    assert len(second.messages) == 2


@pytest.mark.asyncio
async def test_hub_isolates_tenants_and_cleans_failed_sends() -> None:
    hub = RealtimeHub()
    organization_id = uuid.uuid4()
    foreign_organization_id = uuid.uuid4()
    healthy = FakeSocket()
    dead = FakeSocket(fails=True)
    foreign = FakeSocket()
    hub.connect(healthy, organization_id)
    hub.connect(dead, organization_id)
    hub.connect(foreign, foreign_organization_id)

    event = RealtimeEvent(
        type="error",
        organization_id=organization_id,
        conversation_id=uuid.uuid4(),
        payload={"message": "fallo"},
    )
    assert await hub.publish(event) == 1
    assert hub.connection_count(organization_id) == 1
    assert foreign.messages == []
    assert await hub.publish(
        event.model_copy(update={"organization_id": foreign_organization_id})
    ) == 1
    assert foreign.messages[0]["organization_id"] == str(foreign_organization_id)


def _events_auth_context(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, User, str]:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-realtime-jwt-secret-012345678901234567890")
    user = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="realtime@test.invalid",
        password_hash="hash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session = AsyncMock()
    session.get.return_value = user

    async def override_db():
        yield session

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_db)
    return session, user, create_access_token(user.id)


def test_events_ws_rejects_invalid_token_with_4401(monkeypatch: pytest.MonkeyPatch) -> None:
    _session, _user, _token = _events_auth_context(monkeypatch)
    client = TestClient(main.app)
    try:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws/events") as websocket:
                websocket.send_json({"type": "auth", "token": "invalid"})
                websocket.receive_json()
        assert raised.value.code == 4401
    finally:
        client.close()


def test_events_ws_authenticates_and_rejects_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    _session, user, token = _events_auth_context(monkeypatch)
    client = TestClient(main.app)
    try:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws/events") as websocket:
                websocket.send_json({"type": "auth", "token": token})
                assert main.realtime_hub.connection_count(user.organization_id) == 1
                websocket.send_bytes(b"pcm")
                websocket.receive_json()
        assert raised.value.code == 1003
    finally:
        client.close()
    assert main.realtime_hub.connection_count(user.organization_id) == 0


@pytest.mark.asyncio
async def test_call_turn_publishes_error_for_unexpected_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = Mock()
    websocket = FakeSocket()
    organization_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    async def exploding_agent(*args, **kwargs):
        raise RuntimeError("provider exploded")
        yield "token", {"text": "unreachable"}

    monkeypatch.setattr(main, "stream_agent", exploding_agent)
    await main._run_call_turn(
        websocket,
        "Hola",
        [],
        session=session,
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    assert [message["type"] for message in websocket.messages] == [
        "turn.started",
        "error",
    ]
    assert websocket.messages[-1]["message"] == "No se pudo completar la llamada."
