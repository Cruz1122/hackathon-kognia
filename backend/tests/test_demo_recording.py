import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import CallStatus
from app.telephony.bridge import (
    agent_heard_ms,
    catch_up_customer_clock,
    mute_agent_from,
    place_customer_pcm,
    remember_agent_pcm,
)
from app.telephony.recording import recording_store
from app.telephony.runtime import runtime
from app.telephony.sessions import CallSession
from app.telephony.router import _listed, _replayable
from app.telephony.timeline import timeline


def _heard() -> CallSession:
    session = CallSession(
        call_id=uuid.uuid4(),
        token="",
        telnyx_call_control_id="",
        organization_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
    )
    session.recording_offset_ms = 0
    session.monotonic_zero = time.monotonic() - 0.5
    return session


def test_late_customer_audio_does_not_overlap_the_greeting() -> None:
    session = _heard()
    catch_up_customer_clock(session)
    heard_at = agent_heard_ms(session)
    remember_agent_pcm(session, b"\x10\x00" * 320)
    place_customer_pcm(session, b"\x01\x00" * 160, 16000)

    assert heard_at >= 400
    assert len(session.agent_pcm) // 2 > 320
    assert len(session.customer_pcm) // 2 > len(session.agent_pcm) // 2 - 400


def test_confirmed_barge_silences_unplayed_agent_audio() -> None:
    session = _heard()
    remember_agent_pcm(session, b"\x7f\x00" * 100)
    played = len(session.agent_pcm)
    remember_agent_pcm(session, b"\x7f\x00" * 50)
    mute_agent_from(session, played)
    assert session.agent_pcm[:played] == b"\x7f\x00" * 100
    assert session.agent_pcm[played:] == b"\x00" * 100
    assert session.agent_segment_open is False


def test_finished_voice_demo_is_listed_and_replayable() -> None:
    conversation = SimpleNamespace(channel="voice-demo", customer=None)
    ended = SimpleNamespace(
        telnyx_call_control_id=None,
        status=CallStatus.ENDED,
        conversation=conversation,
    )
    active = SimpleNamespace(
        telnyx_call_control_id=None,
        status=CallStatus.ACTIVE,
        conversation=conversation,
    )
    assert _listed(ended) is True
    assert _replayable(ended) is True
    assert _listed(active) is False
    assert _replayable(active) is False


@pytest.mark.asyncio
async def test_demo_session_stores_ready_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELNYX_RECORDINGS_DIR", str(tmp_path / "recordings"))
    recording_store._by_call.clear()
    monkeypatch.setattr("app.telephony.runtime.persist_recording", AsyncMock())
    session = _heard()
    timeline.record(session, "lifecycle", {"state": "ACTIVE"})
    timeline.record(session, "transcript.final", {"speaker": "customer", "text": "Hola"})
    remember_agent_pcm(session, b"\x10\x00" * 1600)
    place_customer_pcm(session, b"\x01\x00" * 1600, 16000)
    timeline.record(session, "transcript.final", {"speaker": "agent", "text": "Buenas"})

    await runtime._store_heard_recording(session)

    record = recording_store.get(session.call_id)
    assert record is not None
    assert record.status == "READY"
    assert record.path is not None and record.path.is_file()
    with wave.open(str(record.path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getframerate() == 16000
    kinds = [event["type"] for event in session.events]
    assert kinds.count("transcript.final") == 2
    assert "lifecycle" in kinds
