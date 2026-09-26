"""JSON jobs over one Redis list; PostgreSQL remains the source of truth."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from .redis import redis_claim, redis_list, redis_move_back, redis_push, redis_remove

QUEUE_NAMESPACE = os.getenv("REDIS_QUEUE_NAMESPACE", "kognia").strip() or "kognia"
JOB_QUEUE_KEY = f"{QUEUE_NAMESPACE}:jobs"
PROCESSING_QUEUE_KEY = f"{QUEUE_NAMESPACE}:jobs:processing"
MAX_JOB_ATTEMPTS = 3
logger = logging.getLogger("hackathon.queue")


@dataclass(frozen=True)
class Job:
    type: str
    organization_id: uuid.UUID
    conversation_id: uuid.UUID
    raw: str | None = field(default=None, compare=False, repr=False)
    attempts: int = field(default=0, compare=True)
    recording_id: uuid.UUID | None = None

    def to_json(self) -> str:
        if self.raw is not None:
            return self.raw
        payload: dict[str, str | int] = {
            "type": self.type,
            "organization_id": str(self.organization_id),
            "conversation_id": str(self.conversation_id),
        }
        if self.attempts:
            payload["attempts"] = self.attempts
        if self.recording_id is not None:
            payload["recording_id"] = str(self.recording_id)
        return json.dumps(payload, separators=(",", ":"))


def parse_job(raw: str | bytes) -> Job:
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Unsupported job")
    kind = payload.get("type")
    if kind not in {"enrich_conversation", "download_recording"}:
        raise ValueError("Unsupported job")
    attempts = payload.get("attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("Invalid job attempts")
    recording_id = None
    if kind == "download_recording":
        recording_id = uuid.UUID(str(payload["recording_id"]))
    return Job(
        type=kind,
        organization_id=uuid.UUID(str(payload["organization_id"])),
        conversation_id=uuid.UUID(str(payload["conversation_id"])),
        raw=raw.decode() if isinstance(raw, bytes) else raw,
        attempts=attempts,
        recording_id=recording_id,
    )


async def enqueue_enrichment(organization_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    try:
        await redis_push(
            JOB_QUEUE_KEY,
            Job("enrich_conversation", organization_id, conversation_id).to_json(),
        )
        return True
    except Exception:
        return False


async def enqueue_recording(
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    recording_id: uuid.UUID,
) -> bool:
    try:
        await redis_push(
            JOB_QUEUE_KEY,
            Job(
                "download_recording",
                organization_id,
                conversation_id,
                recording_id=recording_id,
            ).to_json(),
        )
        return True
    except Exception:
        return False


async def dequeue_job(*, timeout: int = 1) -> Job | None:
    raw = await redis_claim(JOB_QUEUE_KEY, PROCESSING_QUEUE_KEY, timeout=timeout)
    if raw is None:
        return None
    try:
        return parse_job(raw)
    except Exception:
        # Poison messages must not block the queue forever.
        try:
            await redis_remove(PROCESSING_QUEUE_KEY, raw)
        except Exception:
            # The source list has already been claimed. Keep the worker alive;
            # startup recovery can retry cleanup after Redis recovers.
            logger.exception("Could not remove poison job from processing queue")
        raise


async def acknowledge_job(job: Job) -> bool:
    try:
        return bool(await redis_remove(PROCESSING_QUEUE_KEY, job.to_json()))
    except Exception:
        return False


async def requeue_job(job: Job) -> bool:
    raw = job.to_json()
    try:
        if job.attempts >= MAX_JOB_ATTEMPTS:
            acknowledged = await acknowledge_job(job)
            if acknowledged:
                logger.error("Dropping job after %s attempts: %s", MAX_JOB_ATTEMPTS, job.conversation_id)
            return acknowledged
        retry = Job(
            job.type,
            job.organization_id,
            job.conversation_id,
            attempts=job.attempts + 1,
            recording_id=job.recording_id,
        )
        return await redis_move_back(
            PROCESSING_QUEUE_KEY,
            JOB_QUEUE_KEY,
            raw,
            front=True,
            replacement=retry.to_json(),
        )
    except Exception:
        return False


async def recover_inflight_jobs() -> int:
    """Return claimed jobs to the work list after a worker process crash."""
    recovered = 0
    # A short list scan is acceptable here: this runs once at worker startup,
    # not in the request path, and keeps the queue itself framework-free.
    # LPUSH in reverse order places inflight jobs ahead of existing work while
    # retaining their original FIFO order.
    for raw in reversed(await redis_list(PROCESSING_QUEUE_KEY)):
        if not await redis_move_back(PROCESSING_QUEUE_KEY, JOB_QUEUE_KEY, raw, front=True):
            return recovered
        recovered += 1
    return recovered


__all__ = [
    "JOB_QUEUE_KEY",
    "MAX_JOB_ATTEMPTS",
    "PROCESSING_QUEUE_KEY",
    "QUEUE_NAMESPACE",
    "Job",
    "acknowledge_job",
    "dequeue_job",
    "enqueue_enrichment",
    "enqueue_recording",
    "parse_job",
    "recover_inflight_jobs",
    "requeue_job",
]
