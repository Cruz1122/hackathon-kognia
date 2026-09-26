from __future__ import annotations

import logging

from ..db.models import Recording
from ..db.session import get_session_factory
from ..platform.queue import Job
from .recording import RecordingRecord, download_recording, persist_recording

logger = logging.getLogger("hackathon.telnyx.recording")


async def process_recording_job(job: Job) -> None:
    if job.recording_id is None:
        raise ValueError("recording job is missing its id")
    async with get_session_factory()() as session:
        row = await session.get(Recording, job.recording_id)
        if row is None:
            logger.warning("Recording row missing")
            return
        if row.status == "READY" and row.sha256:
            logger.info("Recording already stored")
            return
        record = RecordingRecord(
            id=row.id,
            call_id=row.call_id,
            organization_id=row.organization_id,
            status=row.status,
            download_url=row.download_url,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            duration_ms=row.duration_ms,
            channels=row.channels,
            waveform=row.waveform,
            error=row.error,
        )
    await download_recording(record)
    await persist_recording(record)
