from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .audio import waveform_levels
from .settings import load_settings

logger = logging.getLogger("hackathon.telnyx.recording")
MAX_RECORDING_BYTES = 100 * 1024 * 1024


@dataclass
class RecordingRecord:
    id: uuid.UUID
    call_id: uuid.UUID
    organization_id: uuid.UUID
    status: str = "RECORDING"
    path: Path | None = None
    download_url: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    duration_ms: int | None = None
    channels: int | None = None
    waveform: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0


class RecordingStore:
    def __init__(self) -> None:
        self._by_call: dict[uuid.UUID, RecordingRecord] = {}

    def upsert(self, record: RecordingRecord) -> RecordingRecord:
        current = self._by_call.get(record.call_id)
        if current is None:
            self._by_call[record.call_id] = record
            return record
        return current

    def get(self, call_id: uuid.UUID) -> RecordingRecord | None:
        return self._by_call.get(call_id)


recording_store = RecordingStore()


def _writable_dir() -> Path:
    settings = load_settings()
    try:
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.recordings_dir / ".write"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return settings.recordings_dir
    except OSError:
        fallback = Path("/tmp/kognia-recordings")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def recording_path(record_id: uuid.UUID) -> Path:
    return _writable_dir() / f"{record_id}.wav"


async def download_recording(record: RecordingRecord, *, client: httpx.AsyncClient | None = None) -> RecordingRecord:
    if record.status == "READY" and record.path is not None and record.path.is_file() and record.sha256:
        return record
    if not record.download_url:
        record.status = "ERROR"
        record.error = "missing recording location"
        return record
    record.attempts += 1
    record.status = "DOWNLOADING"
    target = recording_path(record.id)
    temporary = target.with_suffix(".partial")
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    digest = hashlib.sha256()
    size = 0
    try:
        async with http.stream("GET", record.download_url) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RECORDING_BYTES:
                        raise ValueError("recording too large")
                    digest.update(chunk)
                    handle.write(chunk)
        _validate_wav(temporary)
        temporary.replace(target)
        with wave.open(str(target), "rb") as wav_file:
            channels = wav_file.getnchannels()
            rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            pcm = wav_file.readframes(frames)
        record.path = target
        record.sha256 = digest.hexdigest()
        record.size_bytes = size
        record.channels = channels
        record.duration_ms = int(frames / rate * 1000) if rate else 0
        record.waveform = waveform_levels(pcm, rate, channels)
        record.status = "READY"
        record.error = None
        record.download_url = None
        return record
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        record.status = "ERROR"
        record.error = exc.__class__.__name__
        logger.warning("Recording download failed: %s", exc.__class__.__name__)
        return record
    finally:
        if owns_client:
            await http.aclose()


def _validate_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() < 1 or wav_file.getsampwidth() != 2 or wav_file.getframerate() <= 0:
            raise ValueError("invalid wav")


def _apply_recording(row, record: RecordingRecord) -> None:
    row.status = record.status
    row.sha256 = record.sha256
    row.size_bytes = record.size_bytes
    row.duration_ms = record.duration_ms
    row.channels = record.channels
    row.waveform = record.waveform
    row.download_url = record.download_url
    row.error = record.error


async def persist_recording(record: RecordingRecord) -> None:
    from sqlalchemy.exc import IntegrityError

    from ..db.models import Recording
    from ..db.session import get_session_factory

    try:
        async with get_session_factory()() as session:
            row = await session.get(Recording, record.id)
            if row is None:
                row = Recording(
                    id=record.id,
                    call_id=record.call_id,
                    organization_id=record.organization_id,
                    status=record.status,
                )
                session.add(row)
            _apply_recording(row, record)
            try:
                await session.commit()
            except IntegrityError:
                # Two events (recording.saved) can race on the same row; the loser updates.
                await session.rollback()
                row = await session.get(Recording, record.id)
                if row is not None:
                    _apply_recording(row, record)
                    await session.commit()
    except Exception:
        logger.exception("Recording row persist failed")


def public_recording(record: RecordingRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "call_id": str(record.call_id),
        "status": record.status,
        "sha256": record.sha256,
        "size_bytes": record.size_bytes,
        "duration_ms": record.duration_ms,
        "channels": record.channels,
        "waveform": record.waveform,
        "error": record.error,
    }


async def run_download(record: RecordingRecord) -> None:
    await download_recording(record)
    await persist_recording(record)
