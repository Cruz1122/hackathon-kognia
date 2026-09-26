from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from nacl.signing import SigningKey

from app import main
from app.auth.dependencies import get_current_user
from app.db.models import User, UserRole
from app.db.session import get_db
from app.features.transcription import service as sherpa
from app.telephony.audio import pcm16le_to_wire, resolve_byte_order, wav_bytes, wire_to_pcm16le
from app.telephony.bridge import run_agent_turn
from app.telephony.live_audio import live_audio_hub
from app.telephony.marks import MarkTracker
from app.telephony.recording import RecordingRecord, download_recording, recording_store
from app.telephony.runtime import runtime
from app.telephony.sessions import CallSession, registry
from app.telephony.state import project_state
from app.telephony.stt import SherpaSTTProvider
from vendor.patter.transport import stream_url


def _sign(body: bytes, key: SigningKey, timestamp: int) -> dict[str, str]:
    signature = key.sign(f"{timestamp}|".encode() + body).signature
    return {
        "telnyx-signature-ed25519": base64.b64encode(signature).decode(),
        "telnyx-timestamp": str(timestamp),
    }


def _event(event_id: str, event_type: str, payload: dict) -> bytes:
    return json.dumps(
        {"data": {"id": event_id, "event_type": event_type, "occurred_at": "2026-09-26T12:00:00+00:00", "payload": payload}}
    ).encode()


@pytest.fixture
def telnyx_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SigningKey:
    key = SigningKey.generate()
    public = base64.b64encode(bytes(key.verify_key)).decode()
    monkeypatch.setenv("TELNYX_ENABLED", "true")
    monkeypatch.setenv("TELNYX_PUBLIC_KEY", public)
    monkeypatch.setenv("TELNYX_API_KEY", "test-key")
    monkeypatch.setenv("TELNYX_CONNECTION_ID", "connection")
    monkeypatch.setenv("TELNYX_WEBHOOK_HOST", "corner-gorged-calamari.ngrok-free.dev")
    monkeypatch.setenv("TELNYX_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("TELNYX_RECORDINGS_DIR", str(tmp_path / "recordings"))
    registry._by_id.clear()
    registry._by_control.clear()
    registry._events.clear()
    recording_store._by_call.clear()
    runtime.transport = AsyncMock()
    runtime.enable_voice = False
    runtime.agent = None
    runtime._pending_recordings.clear()
    monkeypatch.setattr(runtime, "_bind_tenant", AsyncMock())
    monkeypatch.setattr(runtime, "_persist_call", AsyncMock())
    monkeypatch.setattr("app.telephony.runtime.persist_recording", AsyncMock())
    monkeypatch.setattr("app.platform.queue.enqueue_recording", AsyncMock(return_value=True))
    return key


def _initiated(event_id: str = "evt-1") -> bytes:
    return _event(
        event_id,
        "call.initiated",
        {
            "call_control_id": "cc-1",
            "call_leg_id": "leg-1",
            "call_session_id": "sess-1",
            "direction": "incoming",
            "from": "+15551212",
            "to": "+19847880154",
        },
    )


@pytest.mark.asyncio
async def test_signature_duplicate_and_stream_url(telnyx_key: SigningKey) -> None:
    body = _initiated()
    headers = _sign(body, telnyx_key, int(time.time()))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post("/webhooks/telnyx/voice", content=body, headers=headers)
        again = await client.post("/webhooks/telnyx/voice", content=body, headers=headers)
        bad = await client.post(
            "/webhooks/telnyx/voice",
            content=body,
            headers={"telnyx-signature-ed25519": "aa", "telnyx-timestamp": headers["telnyx-timestamp"]},
        )
        stale = _sign(body, telnyx_key, int(time.time()) - 1000)
        expired = await client.post("/webhooks/telnyx/voice", content=_initiated("evt-old"), headers=stale)
    assert ok.status_code == 200
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert bad.status_code == 403
    assert expired.status_code == 403
    paths = [call.args[1] for call in runtime.transport.await_args_list]
    assert paths.count("/v2/calls/cc-1/actions/answer") == 1
    start = next(call for call in runtime.transport.await_args_list if call.args[1].endswith("streaming_start"))
    assert start.args[2]["stream_bidirectional_codec"] == "L16"
    assert start.args[2]["stream_url"].startswith("wss://corner-gorged-calamari.ngrok-free.dev/ws/telnyx/stream/")
    assert "/ws/call" in {getattr(route, "path", "") for route in main.app.routes}


class _FakeSocket:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.closed: int | None = None

    async def accept(self) -> None:
        return None

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def receive(self) -> dict[str, str]:
        if not self._messages:
            return {"type": "websocket.disconnect"}
        return {"type": "websocket.receive", "text": self._messages.pop(0)}

    async def send_json(self, payload: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_media_socket_rejects_missing_token_and_converts_l16(telnyx_key: SigningKey) -> None:
    body = _initiated()
    headers = _sign(body, telnyx_key, int(time.time()))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhooks/telnyx/voice", content=body, headers=headers)
    assert response.status_code == 200
    session = registry.by_control("cc-1")
    assert session is not None
    rejected = _FakeSocket([])
    await runtime.handle_media(rejected, str(session.call_id), "")
    assert rejected.closed == 1008
    media_format = {"encoding": "L16", "sample_rate": 16000, "channels": 1, "byte_order": "big"}
    wire = pcm16le_to_wire((1).to_bytes(2, "little", signed=True), media_format)
    payload = base64.b64encode(wire).decode()
    socket = _FakeSocket(
        [
            json.dumps({"event": "start", "start": {"media_format": media_format}}),
            json.dumps({"event": "media", "media": {"track": "inbound", "payload": payload}}),
            json.dumps({"event": "stop"}),
        ]
    )
    await runtime.handle_media(socket, str(session.call_id), session.token)
    assert bytes(session.customer_pcm[:2]) == (1).to_bytes(2, "little", signed=True)
    assert resolve_byte_order(session.media_format) == "big"
    again = _FakeSocket([json.dumps({"event": "media", "media": {"track": "inbound", "payload": payload}})])
    await runtime.handle_media(again, str(session.call_id), session.token)
    assert len(session.customer_pcm) == 4


def test_byte_order_and_pcmu_conversion() -> None:
    native = wire_to_pcm16le(b"\x01\x00", {"encoding": "L16", "sample_rate": 16000, "channels": 1})
    assert native == (1).to_bytes(2, "little", signed=True)
    assert resolve_byte_order({"encoding": "L16", "sample_rate": 16000}) == "little"
    big = wire_to_pcm16le(b"\x00\x01", {"encoding": "L16", "sample_rate": 16000, "byte_order": "big"})
    little = wire_to_pcm16le(b"\x01\x00", {"encoding": "L16", "sample_rate": 16000, "byte_order": "little"})
    assert big == little
    mulaw = wire_to_pcm16le(b"\xff" * 160, {"encoding": "PCMU", "sample_rate": 8000, "channels": 1})
    assert len(mulaw) == 640
    started = time.perf_counter()
    wire_to_pcm16le(b"\x00\x01" * 16000, {"encoding": "L16", "sample_rate": 16000, "byte_order": "big"})
    l16_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    wire_to_pcm16le(b"\xff" * 8000, {"encoding": "PCMU", "sample_rate": 8000})
    pcmu_elapsed = time.perf_counter() - started
    assert l16_elapsed < 1
    assert pcmu_elapsed < 1


@pytest.mark.asyncio
async def test_out_of_order_recording_does_not_answer(telnyx_key: SigningKey) -> None:
    saved = _event("evt-rec", "call.recording.saved", {"call_control_id": "cc-9", "recording_urls": {"wav": "https://example.test/a.wav"}})
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhooks/telnyx/voice", content=saved, headers=_sign(saved, telnyx_key, int(time.time())))
    assert response.status_code == 200
    assert runtime.transport.await_count == 0
    assert runtime._pending_recordings["cc-9"].endswith("a.wav")


def test_two_calls_do_not_share_audio(telnyx_key: SigningKey) -> None:
    left = registry.create(telnyx_call_control_id="a", call_leg_id=None, call_session_id=None, caller="1", callee="2")
    right = registry.create(telnyx_call_control_id="b", call_leg_id=None, call_session_id=None, caller="3", callee="4")
    left.organization_id = uuid.uuid4()
    right.organization_id = uuid.uuid4()
    left.customer_pcm.extend(b"\x01\x00")
    right.customer_pcm.extend(b"\x02\x00")
    assert left.customer_pcm != right.customer_pcm
    viewer_left = live_audio_hub.subscribe(left.organization_id, left.call_id)
    live_audio_hub.publish(right.organization_id, right.call_id, b"right")
    live_audio_hub.publish(left.organization_id, left.call_id, b"left")
    assert viewer_left.get_nowait() == b"left"
    live_audio_hub.unsubscribe(left.organization_id, left.call_id, viewer_left)


def test_slow_viewer_drops_oldest() -> None:
    hub_viewer = live_audio_hub.subscribe("org", "call")
    hub_viewer.put_nowait(b"old")
    while not hub_viewer.full():
        hub_viewer.put_nowait(b"fill")
    live_audio_hub.publish("org", "call", b"new")
    frames = []
    while not hub_viewer.empty():
        frames.append(hub_viewer.get_nowait())
    assert frames[-1] == b"new"
    assert b"old" not in frames
    live_audio_hub.unsubscribe("org", "call", hub_viewer)


def test_marks_clear_reconciles_unplayed() -> None:
    marks = MarkTracker()
    first = marks.generated()
    marks.sent(first)
    second = marks.generated()
    assert marks.played(first) == "played"
    cancelled = marks.clear_unplayed()
    assert second in cancelled
    assert marks.played(first) == "played"
    assert marks.state(second) == "cancelled"


def test_project_state_is_stable() -> None:
    events = [
        {"type": "lifecycle", "offset_ms": 0, "payload": {"state": "ACTIVE"}},
        {"type": "transcript.final", "offset_ms": 100, "payload": {"speaker": "customer", "text": "hola"}},
        {"type": "tool.started", "offset_ms": 200, "payload": {"tool": "demo", "tool_call_id": "1"}},
        {"type": "tool.completed", "offset_ms": 300, "payload": {"tool": "demo", "tool_call_id": "1", "status": "ok"}},
        {"type": "audio.cancelled", "offset_ms": 400, "payload": {"name": "mark-1"}},
        {"type": "lifecycle", "offset_ms": 500, "payload": {"state": "ENDED"}},
    ]
    mid = project_state(events, 300)
    again = project_state(events, 300)
    assert mid == again
    assert mid["lifecycle"] == "ACTIVE"
    assert mid["tools"][0]["status"] == "ok"
    assert project_state(events, 0)["transcript"] == []
    assert project_state(events, 500)["lifecycle"] == "ENDED"
    assert [item["type"] for item in project_state(events, 400)["marks"]] == ["audio.cancelled"]


@pytest.mark.asyncio
async def test_agent_cancellation_and_tool_failure() -> None:
    closed = False

    async def hanging_agent(prompt: str, **kwargs):
        nonlocal closed
        try:
            yield "token", {"text": "hola"}
            await asyncio.sleep(30)
        finally:
            closed = True

    session = CallSession(
        call_id=uuid.uuid4(),
        token="token",
        telnyx_call_control_id="cc",
        organization_id=uuid.uuid4(),
    )
    session.marks = MarkTracker()
    task = asyncio.create_task(run_agent_turn(session, "buenas", agent=hanging_agent, tts=None))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed is True

    async def failing_agent(prompt: str, **kwargs):
        yield "tool.started", {"tool": "demo", "tool_call_id": "1"}
        raise RuntimeError("tool failed")

    runtime.agent = failing_agent
    other = CallSession(call_id=uuid.uuid4(), token="token", telnyx_call_control_id="cc2", organization_id=uuid.uuid4())
    other.marks = MarkTracker()
    await runtime._start_turn(other, "hola")
    assert other.turn_task is not None
    await other.turn_task
    assert any(event["type"] == "agent.error" for event in other.events)
    assert any(event["type"] == "tool.started" for event in other.events)


@pytest.mark.asyncio
async def test_sherpa_clones_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        def __init__(self) -> None:
            self.pcm = bytearray()

    def create_stream():
        return Stream()

    def feed_pcm(stream: Stream, pcm: bytes, sample_rate: int = 16000):
        stream.pcm.extend(pcm)
        return (stream.pcm.decode("latin1"), False)

    monkeypatch.setattr(sherpa, "create_stream", create_stream)
    monkeypatch.setattr(sherpa, "feed_pcm", feed_pcm)
    monkeypatch.setattr(sherpa, "finish_stream", lambda stream, sample_rate=16000: "")
    provider = SherpaSTTProvider()
    left = provider.clone()
    right = provider.clone()
    await left.feed(b"a")
    await right.feed(b"b")
    assert left.stream.pcm == b"a"
    assert right.stream.pcm == b"b"
    left.close()
    right.close()


@pytest.mark.asyncio
async def test_recording_download_is_idempotent_and_cleans_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELNYX_RECORDINGS_DIR", str(tmp_path))
    payload = wav_bytes(b"\x01\x00" * 20)
    calls = {"count": 0}

    class Response:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield payload

    class Client:
        def stream(self, method: str, url: str):
            calls["count"] += 1

            class Context:
                async def __aenter__(self):
                    return Response()

                async def __aexit__(self, *exc: object) -> bool:
                    return False

            return Context()

    record = RecordingRecord(id=uuid.uuid4(), call_id=uuid.uuid4(), organization_id=uuid.uuid4(), download_url="https://files.example/rec.wav")
    ready = await download_recording(record, client=Client())
    assert ready.status == "READY"
    assert ready.sha256
    assert ready.download_url is None
    await download_recording(record, client=Client())
    assert calls["count"] == 1

    class Broken:
        def stream(self, method: str, url: str):
            raise RuntimeError("network")

    failed = RecordingRecord(id=uuid.uuid4(), call_id=uuid.uuid4(), organization_id=uuid.uuid4(), download_url="https://files.example/bad.wav")
    result = await download_recording(failed, client=Broken())
    assert result.status == "ERROR"
    assert list(tmp_path.glob("*.partial")) == []


@pytest.mark.asyncio
async def test_replay_range_and_tenant_isolation(telnyx_key: SigningKey, tmp_path: Path) -> None:
    organization_id = uuid.uuid4()
    other_id = uuid.uuid4()
    session = registry.create(telnyx_call_control_id="live", call_leg_id=None, call_session_id=None, caller="+1", callee="+2")
    session.organization_id = organization_id
    session.events = [
        {"seq": 1, "type": "lifecycle", "offset_ms": 0, "payload": {"state": "ACTIVE"}, "occurred_at": "t", "provider_occurred_at": None}
    ]
    record = RecordingRecord(id=uuid.uuid4(), call_id=session.call_id, organization_id=organization_id, status="READY")
    record.path = tmp_path / f"{record.id}.wav"
    record.path.write_bytes(wav_bytes(b"\x00\x00" * 40))
    recording_store._by_call[session.call_id] = record
    user = User(id=uuid.uuid4(), organization_id=organization_id, email="admin@test", password_hash="x", role=UserRole.ADMIN)
    stranger = User(id=uuid.uuid4(), organization_id=other_id, email="other@test", password_hash="x", role=UserRole.ADMIN)

    async def override_db():
        yield AsyncMock()

    main.app.dependency_overrides[get_current_user] = lambda: user
    main.app.dependency_overrides[get_db] = override_db
    try:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            owned = await client.get(f"/calls/{session.call_id}/state?at_ms=0")
            recording = await client.get(f"/calls/{session.call_id}/recording", headers={"Range": "bytes=0-15"})
            main.app.dependency_overrides[get_current_user] = lambda: stranger
            hidden = await client.get(f"/calls/{session.call_id}")
        assert owned.status_code == 200
        assert owned.json()["lifecycle"] == "ACTIVE"
        assert recording.status_code == 206
        assert hidden.status_code == 404
        with wave.open(str(record.path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
    finally:
        main.app.dependency_overrides.clear()


def test_utterance_becomes_ready_on_silence_or_endpoint() -> None:
    from app.telephony.runtime import utterance_ready

    assert utterance_ready(last_voice_at=10, first_voice_at=10, now=10.2, endpoint=False) is False
    assert utterance_ready(last_voice_at=10, first_voice_at=10, now=10.8, endpoint=False) is True
    assert utterance_ready(last_voice_at=0, first_voice_at=0, now=20, endpoint=True) is True
    assert utterance_ready(last_voice_at=1, first_voice_at=1, now=9, endpoint=False) is True


def test_mixed_call_wav_is_what_was_heard() -> None:
    from app.telephony.audio import mixed_call_wav

    wav = mixed_call_wav(b"\x01\x00\x02\x00", b"\x03\x00")
    with wave.open(io.BytesIO(wav)) as handle:
        assert handle.getnchannels() == 1
        frames = handle.readframes(handle.getnframes())
    assert frames == b"\x04\x00\x02\x00"


def test_stream_url_is_wss() -> None:
    assert stream_url("corner-gorged-calamari.ngrok-free.dev", "call", "token").startswith("wss://")
