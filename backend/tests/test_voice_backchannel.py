from __future__ import annotations

import asyncio
import uuid

import pytest

from app import main
from app.platform.tracing import TraceRecorder
from app.telephony.bridge import AudioPlaybackCoordinator
from app.telephony.sessions import CallSession


def _session() -> CallSession:
    return CallSession(
        call_id=uuid.uuid4(),
        token="token",
        telnyx_call_control_id="control",
    )


@pytest.mark.asyncio
async def test_backchannel_is_cancelled_when_final_answer_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_speak(session, voice, text: str, *, audio_kind: str) -> None:
        del session, voice
        calls.append((audio_kind, text))

    monkeypatch.setattr("app.telephony.bridge._speak_raw", fake_speak)
    coordinator = AudioPlaybackCoordinator(_session(), object())
    coordinator.set_agent_guard({"allow_backchannel": True, "stage": "buscando"})
    coordinator.schedule_backchannel("Dame un segundo.")
    await asyncio.sleep(0.01)
    await coordinator.play_final("Respuesta validada.")

    assert calls == [("final", "Respuesta validada.")]


@pytest.mark.asyncio
async def test_backchannel_plays_before_a_slow_final_without_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    active = 0
    max_active = 0

    async def fake_speak(session, voice, text: str, *, audio_kind: str) -> None:
        nonlocal active, max_active
        del session, voice
        active += 1
        max_active = max(max_active, active)
        calls.append((audio_kind, text))
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr("app.telephony.bridge._speak_raw", fake_speak)
    coordinator = AudioPlaybackCoordinator(_session(), object())
    coordinator.set_agent_guard({"allow_backchannel": True, "stage": "buscando"})
    coordinator.schedule_backchannel("Voy a revisar esa información.")
    await asyncio.sleep(0.73)
    await coordinator.play_final("Encontré la información.")

    assert calls == [
        ("backchannel", "Voy a revisar esa información."),
        ("final", "Encontré la información."),
    ]
    assert max_active == 1


@pytest.mark.asyncio
async def test_structural_guard_can_skip_backchannel_without_text_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_speak(session, voice, text: str, *, audio_kind: str) -> None:
        del session, voice, text
        calls.append(audio_kind)

    monkeypatch.setattr("app.telephony.bridge._speak_raw", fake_speak)
    coordinator = AudioPlaybackCoordinator(_session(), object())
    coordinator.set_agent_guard({"allow_backchannel": False, "stage": "emergencia"})
    coordinator.schedule_backchannel("Dame un segundo.")
    await asyncio.sleep(0.73)
    await coordinator.cancel_backchannel()

    assert calls == []


@pytest.mark.asyncio
async def test_simple_turn_does_not_receive_a_waiting_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_speak(session, voice, text: str, *, audio_kind: str) -> None:
        del session, voice, text
        calls.append(audio_kind)

    monkeypatch.setattr("app.telephony.bridge._speak_raw", fake_speak)
    coordinator = AudioPlaybackCoordinator(_session(), object())
    coordinator.set_agent_guard({"allow_backchannel": True, "stage": "inicio"})
    coordinator.schedule_backchannel("Dame un segundo.")
    await asyncio.sleep(0.73)
    await coordinator.cancel_backchannel()

    assert calls == []


def test_trace_recorder_keeps_perceived_voice_timings_separate() -> None:
    recorder = TraceRecorder(call_id="call")
    recorder.record_voice_timing(first_audio_ms=712, useful_answer_ms=4630)
    assert recorder.to_dict()["voice"] == {
        "time_to_first_audio_ms": 712,
        "time_to_useful_answer_ms": 4630,
    }


@pytest.mark.asyncio
async def test_browser_call_path_plays_one_cached_backchannel(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_speak(websocket, text, *, organization_id, conversation_id, heard, audio_kind, cached_audio=None) -> None:
        del websocket, organization_id, conversation_id, heard, cached_audio
        calls.append((audio_kind, text))

    class CachedTTS:
        def cached_audio(self, text: str) -> tuple[int, bytes] | None:
            return (22050, b"pcm") if text else None

    monkeypatch.setattr(main, "_speak_chunk", fake_speak)
    monkeypatch.setattr(main, "tts_provider", CachedTTS())
    session = _session()
    coordinator = main.BrowserAudioPlaybackCoordinator(
        object(),
        organization_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        heard=session,
    )
    coordinator.set_agent_guard({"allow_backchannel": True, "stage": "buscando"})
    coordinator.schedule_backchannel("Dame un segundo.")
    await asyncio.sleep(0.73)
    await coordinator.play_final("Respuesta breve.")

    assert calls == [
        ("backchannel", "Dame un segundo."),
        ("final", "Respuesta breve."),
    ]


@pytest.mark.asyncio
async def test_browser_backchannel_emits_a_waiting_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_event(websocket, event_type, payload, **kwargs) -> None:
        del websocket, kwargs
        events.append((event_type, payload))

    class Socket:
        async def send_json(self, _payload) -> None:
            return None

        async def send_bytes(self, _payload) -> None:
            return None

    monkeypatch.setattr(main, "_send_call_event", fake_event)
    monkeypatch.setattr(main, "tts_status", "ready")

    await main._speak_chunk(
        Socket(),
        "Dame un segundo.",
        organization_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        audio_kind="backchannel",
        cached_audio=(22050, b"\x01\x00"),
    )

    assert ("agent.waiting", {"text": "Dame un segundo.", "audio_kind": "backchannel"}) in events


@pytest.mark.asyncio
async def test_browser_call_path_skips_backchannel_for_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_speak(*args, **kwargs) -> None:
        del args
        calls.append(kwargs["audio_kind"])

    class CachedTTS:
        def cached_audio(self, text: str) -> tuple[int, bytes] | None:
            return (22050, b"pcm") if text else None

    monkeypatch.setattr(main, "_speak_chunk", fake_speak)
    monkeypatch.setattr(main, "tts_provider", CachedTTS())
    coordinator = main.BrowserAudioPlaybackCoordinator(
        object(),
        organization_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        heard=_session(),
    )
    coordinator.set_agent_guard({"allow_backchannel": True, "stage": "inicio"})
    coordinator.schedule_backchannel("Dame un segundo.")
    await asyncio.sleep(0.73)
    await coordinator.cancel_backchannel()

    assert calls == []
