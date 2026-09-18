from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.auth.tokens import create_access_token
from app.db.models import Conversation, Message as DbMessage, MessageRole, User, UserRole
from app.db.session import get_db
from app.providers import FakeSTT

TEST_SECRET = "test-conversation-jwt-secret-012345678901234567890"


def _admin() -> User:
    return User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="admin@conversation.test",
        password_hash="hash",
        role=UserRole.ADMIN,
        is_active=True,
    )


def _conversation(user: User) -> Conversation:
    now = datetime.now(UTC)
    return Conversation(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        created_by=user.id,
        channel="voice-demo",
        status="open",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def auth_context(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, User, dict[str, str]]:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    user = _admin()
    session = AsyncMock()
    session.add = Mock()
    session.get.return_value = user

    async def override_db():
        yield session

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_db)
    token = create_access_token(user.id)
    return session, user, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_and_reload_conversation_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, user, headers = auth_context
    conversation = _conversation(user)
    old_message = DbMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="Hola",
        created_at=datetime.now(UTC),
    )

    async def fake_create(*args, **kwargs) -> Conversation:
        assert kwargs["organization_id"] == user.organization_id
        assert kwargs["created_by"] == user.id
        return conversation

    async def fake_history(*args, **kwargs):
        assert kwargs["organization_id"] == user.organization_id
        assert kwargs["conversation_id"] == conversation.id
        return conversation, [old_message]

    monkeypatch.setattr(main, "create_conversation", fake_create)
    monkeypatch.setattr(main, "_conversation_history", fake_history)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/conversations",
            headers=headers,
            json={"channel": "voice-demo"},
        )
        reloaded = await client.get(f"/conversations/{conversation.id}", headers=headers)

    assert created.status_code == 201
    assert created.json()["organization_id"] == str(user.organization_id)
    assert reloaded.status_code == 200
    assert reloaded.json()["messages"][0]["content"] == "Hola"


@pytest.mark.asyncio
async def test_conversation_lookup_does_not_cross_tenant(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, user, headers = auth_context
    requested_id = uuid.uuid4()

    async def foreign_filtered_history(*args, **kwargs):
        assert kwargs["organization_id"] == user.organization_id
        raise HTTPException(status_code=404, detail="Conversation not found.")

    monkeypatch.setattr(main, "_conversation_history", foreign_filtered_history)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/conversations/{requested_id}", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found."}


@pytest.mark.asyncio
async def test_ask_uses_db_history_and_persists_only_final_messages(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    session, user, headers = auth_context
    conversation = _conversation(user)
    existing = DbMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="historial DB",
        created_at=datetime.now(UTC),
    )
    persisted: list[tuple[MessageRole, str]] = []

    async def fake_history(*args, **kwargs):
        return conversation, [existing]

    async def fake_persist(_session, *, conversation_id, role, content):
        assert conversation_id == conversation.id
        persisted.append((role, content))
        return DbMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )

    async def fake_agent(prompt, *, messages, llm=None):
        assert prompt == "nuevo turno"
        assert messages == [{"role": "user", "content": "historial DB"}]
        yield "token", {"text": "respuesta final"}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(main, "_conversation_history", fake_history)
    monkeypatch.setattr(main, "_persist_message", fake_persist)
    monkeypatch.setattr(main, "stream_agent", fake_agent)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=headers,
            json={"conversation_id": str(conversation.id), "prompt": "nuevo turno"},
        )

    assert response.status_code == 200
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert persisted == [
        (MessageRole.USER, "nuevo turno"),
        (MessageRole.ASSISTANT, "respuesta final"),
    ]
    assert session.add.call_count == 0


@pytest.mark.asyncio
async def test_provider_error_keeps_user_message_without_assistant(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, user, headers = auth_context
    conversation = _conversation(user)
    persisted: list[MessageRole] = []

    async def fake_history(*args, **kwargs):
        return conversation, []

    async def fake_persist(_session, *, conversation_id, role, content):
        persisted.append(role)
        return DbMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )

    async def failed_agent(prompt, *, messages, llm=None):
        yield "error", {"message": "No hay proveedores disponibles"}

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(main, "_conversation_history", fake_history)
    monkeypatch.setattr(main, "_persist_message", fake_persist)
    monkeypatch.setattr(main, "stream_agent", failed_agent)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            headers=headers,
            json={"conversation_id": str(conversation.id), "prompt": "fallará"},
        )

    assert response.status_code == 502
    assert persisted == [MessageRole.USER]


def test_ws_rejects_invalid_token_with_4401(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, _user, _headers = auth_context
    client = TestClient(main.app)
    try:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws/call") as websocket:
                websocket.send_json({"type": "auth", "token": "invalid"})
                websocket.receive_json()
        assert raised.value.code == 4401
    finally:
        client.close()


def test_ws_rejects_binary_before_authentication(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, _user, _headers = auth_context
    client = TestClient(main.app)
    try:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws/call") as websocket:
                websocket.send_bytes(b"pcm-before-auth")
                websocket.receive_json()
        assert raised.value.code == 4401
    finally:
        client.close()


def test_ws_attaches_conversation_and_persists_user_and_assistant(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        assert kwargs["organization_id"] == user.organization_id
        assert kwargs["conversation_id"] == conversation.id
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return []

    async def fake_agent(prompt, *, messages, llm=None):
        assert prompt == "Hola desde WS"
        assert messages is None
        yield "token", {"text": "respuesta WS"}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", fake_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            connected = websocket.receive_json()
            assert connected["type"] == "call.connected"
            assert connected["conversation_id"] == str(conversation.id)

            websocket.send_json(
                {
                    "type": "turn",
                    "prompt": "Hola desde WS",
                    "messages": [{"role": "user", "content": "client-controlled history"}],
                }
            )
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "turn.completed":
                    break
    finally:
        client.close()

    assert [event["type"] for event in events] == [
        "turn.started",
        "agent.token",
        "turn.completed",
    ]
    stored = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], DbMessage)]
    assert [(message.role, message.content) for message in stored] == [
        (MessageRole.USER, "Hola desde WS"),
        (MessageRole.ASSISTANT, "respuesta WS"),
    ]


def test_ws_does_not_attach_foreign_conversation(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, user, _headers = auth_context

    async def foreign_conversation(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "get_conversation", foreign_conversation)
    token = create_access_token(user.id)
    client = TestClient(main.app)
    try:
        with pytest.raises(WebSocketDisconnect) as raised:
            with client.websocket_connect("/ws/call") as websocket:
                websocket.send_json({"type": "auth", "token": token})
                websocket.send_json(
                    {"type": "conversation.attach", "conversation_id": str(uuid.uuid4())}
                )
                websocket.receive_json()
        assert raised.value.code == 4403
    finally:
        client.close()
