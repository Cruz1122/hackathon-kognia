from __future__ import annotations

import os
import uuid

import pytest

# Keep this integration queue separate from a running Compose worker. The
# production default remains ``kognia``.
if os.getenv("RUN_REDIS_INTEGRATION") == "1":
    os.environ.setdefault("REDIS_QUEUE_NAMESPACE", f"test-{uuid.uuid4().hex}")

from app.platform.queue import (
    JOB_QUEUE_KEY,
    PROCESSING_QUEUE_KEY,
    acknowledge_job,
    dequeue_job,
    enqueue_enrichment,
    requeue_job,
)
from app.platform.redis import close_redis, get_redis_client


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_INTEGRATION") != "1",
    reason="set RUN_REDIS_INTEGRATION=1 to run Redis queue integration tests",
)


@pytest.mark.asyncio
async def test_redis_queue_requeues_with_incremented_attempts() -> None:
    client = get_redis_client()
    await client.delete(JOB_QUEUE_KEY, PROCESSING_QUEUE_KEY)
    organization_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    try:
        assert await enqueue_enrichment(organization_id, conversation_id) is True
        job = await dequeue_job(timeout=1)
        assert job is not None
        assert job.attempts == 0
        assert await requeue_job(job) is True

        retry = await dequeue_job(timeout=1)
        assert retry is not None
        assert retry.organization_id == organization_id
        assert retry.conversation_id == conversation_id
        assert retry.attempts == 1
        assert await acknowledge_job(retry) is True
    finally:
        await client.delete(JOB_QUEUE_KEY, PROCESSING_QUEUE_KEY)
        await close_redis()
