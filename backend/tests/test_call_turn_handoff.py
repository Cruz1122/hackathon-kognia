import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import main
from app.providers.fakes import FakeSTT


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_before_confirmation", [False, True])
async def test_confirmed_barge_releases_browser_playback_after_generation_finishes(
    monkeypatch: pytest.MonkeyPatch, finish_before_confirmation: bool,
) -> None:
    incoming: asyncio.Queue[dict] = asyncio.Queue()
    outgoing: asyncio.Queue[dict] = asyncio.Queue()
    sent: list[dict] = []
    greeting_started = asyncio.Event()
    finish_greeting = asyncio.Event()
    conversation_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())

    class Socket:
        accept = AsyncMock()
        close = AsyncMock()

        async def receive(self):
            return await incoming.get()

        async def send_json(self, payload):
            sent.append(payload)
            await outgoing.put(payload)

    def send(payload):
        incoming.put_nowait({"type": "websocket.receive", "text": json.dumps(payload)})

    async def wait_for(kind):
        async with asyncio.timeout(3):
            while (await outgoing.get())["type"] != kind:
                pass

    async def speak_greeting(*args, **kwargs):
        greeting_started.set()
        await finish_greeting.wait()

    async def reply(socket, prompt, *args, **kwargs):
        assert prompt == "Necesito otra consulta"
        await socket.send_json({"type": "turn.started"})
        await socket.send_json({"type": "turn.completed"})
        return False

    session = AsyncMock()
    session.add = Mock()
    monkeypatch.setattr(main, "authenticate_token", AsyncMock(return_value=user))
    monkeypatch.setattr(main, "get_conversation", AsyncMock(return_value=SimpleNamespace(id=conversation_id)))
    monkeypatch.setattr(main, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "_persist_message", AsyncMock())
    monkeypatch.setattr(main, "_persist_recording_origin", AsyncMock())
    monkeypatch.setattr(main, "_finish_call", AsyncMock())
    monkeypatch.setattr(main.telephony_runtime, "_store_heard_recording", AsyncMock())
    monkeypatch.setattr(main.timeline, "record", Mock())
    monkeypatch.setattr(main, "_speak_chunk", speak_greeting)
    monkeypatch.setattr(main, "_run_call_turn", reply)
    monkeypatch.setattr(main, "stt_provider", FakeSTT("Necesito otra consulta"))
    monkeypatch.setattr(main, "tts_status", "error")
    monkeypatch.setattr(main, "utterance_ready", lambda **kwargs: True)
    monkeypatch.setattr(main, "CALL_BARGE_GRACE_SECONDS", 60)
    send({"type": "auth", "token": "test"})
    send({"type": "conversation.attach", "conversation_id": str(conversation_id)})
    task = asyncio.create_task(main.call_socket(Socket(), session))
    try:
        await asyncio.wait_for(greeting_started.wait(), 3)
        send({"type": "barge"})
        await wait_for("tts.pause")
        if finish_before_confirmation:
            finish_greeting.set()
            await wait_for("turn.completed")
        incoming.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x00" * 640})
        await wait_for("customer.transcript")
        await wait_for("turn.completed")
        kinds = [event["type"] for event in sent]
        # Generation ending does not mean that the browser has played its queue.
        assert "tts.cancel" in kinds
        assert kinds.index("tts.pause") < kinds.index("tts.cancel") < kinds.index("customer.transcript")
    finally:
        incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)
