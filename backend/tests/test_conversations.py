from __future__ import annotations

import asyncio
import math
import struct
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
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


def test_ws_fresh_demo_greets_and_answers_hola_without_false_apology(
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
        assert prompt == "Hola"
        assert messages == [{"role": "assistant", "content": main._call_demo_greeting()}]
        yield "token", {"text": "Hola, ¿en qué te puedo ayudar?"}
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
            assert websocket.receive_json()["type"] == "turn.started"
            welcome = websocket.receive_json()
            assert welcome == {"type": "agent.token", "text": main._call_demo_greeting()}
            assert "asistente de información de IPS" in welcome["text"]
            assert "capacidades registradas" in welcome["text"]
            assert websocket.receive_json()["type"] == "turn.completed"

            websocket.send_json(
                {
                    "type": "turn",
                    "prompt": "Hola",
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
    answer = next(event["text"] for event in events if event["type"] == "agent.token")
    assert "¿en qué te puedo ayudar?" in answer
    assert "no te escuché" not in answer.lower()
    assert "perdón" not in answer.lower()
    stored = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], DbMessage)]
    assert [(message.role, message.content) for message in stored] == [
        (MessageRole.ASSISTANT, main._call_demo_greeting()),
        (MessageRole.USER, "Hola"),
        (MessageRole.ASSISTANT, "Hola, ¿en qué te puedo ayudar?"),
    ]


@pytest.mark.parametrize("booking_succeeded", [True, False])
def test_ws_demo_requests_hangup_only_after_successful_booking(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
    booking_succeeded: bool,
) -> None:
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="¿Cómo te llamas?")]

    prompts: list[str] = []

    async def fake_agent(prompt, *, messages, llm=None):
        prompts.append(prompt)
        yield "tool.completed", {"tool": "create_booking", "ok": booking_succeeded}
        yield "token", {"text": "Tu reserva está confirmada. Gracias por llamar."}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stream_agent", fake_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json({"type": "conversation.attach", "conversation_id": str(conversation.id)})
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "turn", "prompt": "sí, confirmo"})
            while True:
                event = websocket.receive_json()
                if event["type"] == "turn.completed":
                    break
            if booking_succeeded:
                websocket.send_json({"type": "turn", "prompt": "¿Continuamos?"})
                websocket.close()
    finally:
        client.close()

    assert event["end_call"] is booking_succeeded
    assert prompts == ["sí, confirmo"]


def test_ws_call_forwards_only_last_context_messages(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)
    persisted = [
        DbMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            content=f"m{index}",
            channel="voice",
            created_at=datetime.now(UTC),
        )
        for index in range(24)
    ]
    captured: list[dict[str, str]] = []

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return persisted

    async def fake_agent(prompt, *, messages, llm=None):
        captured.extend(messages or [])
        yield "token", {"text": "ok"}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stream_agent", fake_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "turn", "prompt": "Y ahora"})
            while True:
                if websocket.receive_json()["type"] == "turn.completed":
                    break
    finally:
        client.close()

    spoken = [
        {"role": message.role.value, "content": message.content}
        for message in persisted
        if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]
    assert captured == spoken


def test_ws_barge_holds_and_resumes_without_customer_speech(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """A noise barge must pause playback and resume it when no utterance follows."""
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "respuesta que el usuario interrumpe"}
        await asyncio.sleep(30)
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", slow_agent)
    monkeypatch.setattr(main, "tts_status", "error")
    monkeypatch.setattr(main, "CALL_BARGE_GRACE_SECONDS", 0.05)

    loud = b"\x00\x40" * 1600  # int16 0x4000 → strong energy frame
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"

            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "turn.started":
                    break

            # The client hears noise and barges, then goes silent.
            websocket.send_json({"type": "barge"})
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "tts.resume":
                    break
    finally:
        client.close()

    types = [event["type"] for event in events]
    assert "tts.pause" in types
    assert "tts.cancel" not in types
    assert "turn.cancelled" not in types


def test_ws_pcm_stop_releases_held_playback(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """Restarting capture must not leave the client's audio queue suspended forever."""
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "respuesta larga"}
        await asyncio.sleep(30)
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", slow_agent)
    monkeypatch.setattr(main, "tts_status", "error")
    # Keep the grace timer far away so only pcm.stop can release the held playback.
    monkeypatch.setattr(main, "CALL_BARGE_GRACE_SECONDS", 30.0)

    loud = b"\x00\x40" * 1600
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"

            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            while websocket.receive_json()["type"] != "turn.started":
                pass

            websocket.send_json({"type": "barge"})
            while websocket.receive_json()["type"] != "tts.pause":
                pass

            websocket.send_json({"type": "pcm.stop"})
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "tts.resume":
                    break
    finally:
        client.close()

    assert "tts.resume" in [event["type"] for event in events]


def test_ws_barge_cancels_active_turn_when_customer_speaks(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """Once the customer really speaks, the held turn must be cancelled."""
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "respuesta que el usuario interrumpe"}
        await asyncio.sleep(30)
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT(text="quiero reservar"))
    monkeypatch.setattr(main, "stream_agent", slow_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    loud = b"\x00\x40" * 1600
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"

            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            while websocket.receive_json()["type"] != "turn.started":
                pass

            # A barge arrives, then real speech: the held turn must be confirmed as interrupted.
            websocket.send_json({"type": "barge"})
            websocket.send_bytes(loud)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "turn.started":
                    break
    finally:
        client.close()

    types = [event["type"] for event in events]
    assert "tts.cancel" in types
    assert "turn.cancelled" in types


def test_ws_voice_energy_cancels_active_turn(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "respuesta larga"}
        await asyncio.sleep(30)
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", slow_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    loud = b"\x00\x40" * 1600
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"

            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            while websocket.receive_json()["type"] != "turn.started":
                pass

            time.sleep(main.CALL_BARGE_ARM_SECONDS + 0.2)
            websocket.send_bytes(loud)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "turn.cancelled":
                    break
    finally:
        client.close()

    types = [event["type"] for event in events]
    assert "tts.cancel" in types
    assert "turn.cancelled" in types


def test_ws_interrupt_after_turn_completed_still_transcribes(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """A barge after the speak task finished drops buffered audio and still hears the next utterance."""
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def fast_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "respuesta corta"}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", fast_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    loud = b"\x00\x40" * 1600
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"

            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            while websocket.receive_json()["type"] != "turn.completed":
                pass

            # The user corrects while the client is still playing the buffered reply.
            websocket.send_json({"type": "barge"})
            websocket.send_bytes(loud)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "turn.started":
                    break
    finally:
        client.close()

    types = [event["type"] for event in events]
    assert "tts.cancel" in types
    assert "tts.resume" not in types
    assert "customer.transcript" in types
    assert "turn.started" in types


def test_ws_booking_completion_ignores_turns_after_audio_playback_finishes(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """A confirmed booking must leave the call unable to create another agent turn."""
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)
    prompts: list[str] = []

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    async def booking_agent(prompt, *, messages, llm=None):
        prompts.append(prompt)
        yield "tool.completed", {"tool": "create_booking", "ok": True}
        yield "token", {"text": "Tu reserva quedó confirmada. Gracias por llamar."}
        yield "done", {"provider": "fake", "model": "fake"}

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", FakeSTT())
    monkeypatch.setattr(main, "stream_agent", booking_agent)
    monkeypatch.setattr(main, "tts_status", "error")

    loud = b"\x00\x40" * 1600
    client = TestClient(main.app)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json(
                {"type": "conversation.attach", "conversation_id": str(conversation.id)}
            )
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)

            completed = None
            while True:
                event = websocket.receive_json()
                if event["type"] == "turn.completed":
                    completed = event
                    break

            assert completed["end_call"] is True
            # This frame reaps the completed turn before any new silence/turn logic runs.
            websocket.send_bytes(b"\x00\x00" * 160)
            websocket.send_json({"type": "turn", "prompt": "¿Continuamos?"})
            websocket.close()
    finally:
        client.close()

    assert prompts == ["transcripción local"]


def _quiet_syllable(samples: int = 1600) -> bytes:
    """Soft 200 Hz tone: not voiced (RMS under 0.016) but above the speech floor."""
    values = [int(650 * math.sin(2 * math.pi * 200 * index / 16000)) for index in range(samples)]
    return struct.pack(f"<{samples}h", *values)


class _SyllableSTT:
    """Returns sí only after non-silent audio, and never endpoints a quiet frame."""

    def __init__(self) -> None:
        self.pcm = bytearray()

    def preload(self) -> None:
        return None

    def create_stream(self) -> dict[str, str]:
        return {"kind": "syllable"}

    def reset_stream(self, stream: object) -> None:
        del stream
        self.pcm.clear()

    def feed_pcm(self, stream: object, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
        del stream, sample_rate
        self.pcm.extend(pcm)
        count = len(pcm) // 2
        energy = 0.0
        if count:
            for sample in struct.unpack(f"<{count}h", pcm[: count * 2]):
                energy += sample * sample
        rms = (energy / count) ** 0.5 / 32768.0 if count else 0.0
        # The loud probe is a flat burst. After the high-pass it is a click, not the raw prefix.
        return "", bool(pcm) and (pcm[:2] == b"\x00\x40" or rms >= 0.03)

    def finish_stream(self, stream: object, sample_rate: int = 16000) -> str:
        del stream, sample_rate
        return "sí" if any(self.pcm) else ""

    def pcm_wave_level(self, pcm: bytes, sample_rate: int = 16000) -> float:
        del pcm, sample_rate
        return 0.2

    def transcribe_audio(self, audio: bytes, content_type: str) -> str:
        del audio, content_type
        return "sí"


def _attach_call(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
    *,
    agent,
) -> tuple[TestClient, str, Conversation]:
    _session, user, _headers = auth_context
    conversation = _conversation(user)
    token = create_access_token(user.id)

    async def fake_get_conversation(*args, **kwargs):
        return conversation

    async def fake_list_messages(*args, **kwargs):
        return [SimpleNamespace(role=MessageRole.ASSISTANT, content="Bienvenida previa")]

    monkeypatch.setattr(main, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(main, "list_messages", fake_list_messages)
    monkeypatch.setattr(main, "stt_provider", _SyllableSTT())
    monkeypatch.setattr(main, "stream_agent", agent)
    monkeypatch.setattr(main, "tts_status", "error")
    return TestClient(main.app), token, conversation


def _events_until(websocket, event_type: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["type"] == event_type:
            return events


def test_ws_quiet_monosyllable_starts_turn_after_silence(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """A short unvoiced burst below the old 0.02 gate still becomes a turn."""

    async def fast_agent(prompt, *, messages, llm=None):
        assert prompt == "sí"
        yield "token", {"text": "Listo."}
        yield "done", {"provider": "fake", "model": "fake"}

    client, token, conversation = _attach_call(monkeypatch, auth_context, agent=fast_agent)
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json({"type": "conversation.attach", "conversation_id": str(conversation.id)})
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(_quiet_syllable())
            time.sleep(main.CALL_SILENCE_SECONDS + 0.05)
            websocket.send_bytes(b"\x00\x00" * 1600)
            events = _events_until(websocket, "customer.transcript")
    finally:
        client.close()

    assert events[-1]["text"] == "sí"


def test_ws_empty_stt_recovers_a_lone_si(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """Sherpa returns nothing for a lone sí; the helper-word pass restores it."""

    async def fast_agent(prompt, *, messages, llm=None):
        assert prompt == "Si"
        yield "token", {"text": "Listo."}
        yield "done", {"provider": "fake", "model": "fake"}

    def recover(pcm: bytes, sample_rate: int = 16000) -> str:
        return "Si" if any(pcm) else ""

    monkeypatch.setattr(main, "recover_short_transcript", recover)
    client, token, conversation = _attach_call(monkeypatch, auth_context, agent=fast_agent)
    monkeypatch.setattr(main, "stt_provider", _EmptySTT())
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json({"type": "conversation.attach", "conversation_id": str(conversation.id)})
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(_quiet_syllable())
            time.sleep(main.CALL_SILENCE_SECONDS + 0.05)
            websocket.send_bytes(b"\x00\x00" * 1600)
            events = _events_until(websocket, "customer.transcript")
    finally:
        client.close()

    assert events[-1]["text"] == "Si"


class _EmptySTT(_SyllableSTT):
    def feed_pcm(self, stream: object, pcm: bytes, sample_rate: int = 16000) -> tuple[str, bool]:
        del stream, sample_rate
        self.pcm.extend(pcm)
        return "", False

    def finish_stream(self, stream: object, sample_rate: int = 16000) -> str:
        del stream, sample_rate
        return ""


def test_ws_overlapping_monosyllable_survives_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    """Sí spoken over the end of the agent reply is kept; silence during the reply is not."""

    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "¿La confirmas?"}
        await asyncio.sleep(0.9)
        yield "done", {"provider": "fake", "model": "fake"}

    client, token, conversation = _attach_call(monkeypatch, auth_context, agent=slow_agent)
    loud = b"\x00\x40" * 1600
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json({"type": "conversation.attach", "conversation_id": str(conversation.id)})
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            assert _events_until(websocket, "turn.started")
            time.sleep(0.6)
            websocket.send_bytes(_quiet_syllable())
            during = _events_until(websocket, "turn.completed")
            assert "tts.cancel" not in [event["type"] for event in during]
            assert "turn.cancelled" not in [event["type"] for event in during]
            websocket.send_bytes(b"\x00\x00" * 1600)
            time.sleep(main.CALL_SILENCE_SECONDS + 0.05)
            websocket.send_bytes(b"\x00\x00" * 1600)
            events = _events_until(websocket, "customer.transcript")
    finally:
        client.close()

    assert events[-1]["text"] == "sí"


def test_ws_silence_during_agent_turn_does_not_start_transcript(
    monkeypatch: pytest.MonkeyPatch,
    auth_context: tuple[AsyncMock, User, dict[str, str]],
) -> None:
    async def slow_agent(prompt, *, messages, llm=None):
        yield "token", {"text": "¿La confirmas?"}
        await asyncio.sleep(0.4)
        yield "done", {"provider": "fake", "model": "fake"}

    client, token, conversation = _attach_call(monkeypatch, auth_context, agent=slow_agent)
    loud = b"\x00\x40" * 1600
    try:
        with client.websocket_connect("/ws/call") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.send_json({"type": "conversation.attach", "conversation_id": str(conversation.id)})
            assert websocket.receive_json()["type"] == "call.connected"
            websocket.send_json({"type": "pcm.start", "sample_rate": 16000})
            websocket.send_bytes(loud)
            assert _events_until(websocket, "turn.started")
            websocket.send_bytes(b"\x00\x00" * 1600)
            assert _events_until(websocket, "turn.completed")
            time.sleep(main.CALL_SILENCE_SECONDS + 0.05)
            websocket.send_bytes(b"\x00\x00" * 1600)
            time.sleep(main.CALL_SILENCE_SECONDS + 0.05)
            websocket.send_bytes(b"\x00\x00" * 1600)
            websocket.send_json({"type": "nope"})
            events = _events_until(websocket, "error")
    finally:
        client.close()

    assert "customer.transcript" not in [event["type"] for event in events]


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
