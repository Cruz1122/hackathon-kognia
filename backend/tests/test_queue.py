from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.platform import queue
from app.platform.queue import Job


def test_job_json_round_trip_and_rejects_invalid_payloads() -> None:
    job = Job("enrich_conversation", uuid.uuid4(), uuid.uuid4())
    assert queue.parse_job(job.to_json()) == job
    for raw in ("not-json", "[]", '{"type":"other"}', '{"type":"enrich_conversation"}'):
        with pytest.raises((ValueError, KeyError)):
            queue.parse_job(raw)


@pytest.mark.asyncio
async def test_enqueue_handles_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    push = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(queue, "redis_push", push)
    assert await queue.enqueue_enrichment(uuid.uuid4(), uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_dequeue_claims_and_acknowledges_json_job(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job("enrich_conversation", uuid.uuid4(), uuid.uuid4())
    monkeypatch.setattr(queue, "redis_claim", AsyncMock(return_value=job.to_json()))
    remove = AsyncMock(return_value=1)
    monkeypatch.setattr(queue, "redis_remove", remove)

    assert await queue.dequeue_job() == job
    assert await queue.acknowledge_job(job) is True
    remove.assert_awaited_once_with(queue.PROCESSING_QUEUE_KEY, job.to_json())


@pytest.mark.asyncio
async def test_dequeue_removes_poison_job_from_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{"type":"unsupported"}'
    remove = AsyncMock(return_value=1)
    monkeypatch.setattr(queue, "redis_claim", AsyncMock(return_value=raw))
    monkeypatch.setattr(queue, "redis_remove", remove)
    with pytest.raises(ValueError):
        await queue.dequeue_job()
    remove.assert_awaited_once_with(queue.PROCESSING_QUEUE_KEY, raw)


@pytest.mark.asyncio
async def test_dequeue_keeps_original_error_when_poison_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{"type":"unsupported"}'
    monkeypatch.setattr(queue, "redis_claim", AsyncMock(return_value=raw))
    monkeypatch.setattr(queue, "redis_remove", AsyncMock(side_effect=RuntimeError("redis down")))

    with pytest.raises(ValueError, match="Unsupported job"):
        await queue.dequeue_job()


@pytest.mark.asyncio
async def test_requeue_preserves_original_payload_and_recovers_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{ "conversation_id": "%s", "organization_id": "%s", "type": "enrich_conversation" }' % (uuid.uuid4(), uuid.uuid4())
    job = queue.parse_job(raw)
    move = AsyncMock(return_value=True)
    monkeypatch.setattr(queue, "redis_move_back", move)
    assert await queue.requeue_job(job) is True
    move.assert_awaited_once_with(
        queue.PROCESSING_QUEUE_KEY,
        queue.JOB_QUEUE_KEY,
        raw,
        front=True,
        replacement=queue.Job(
            "enrich_conversation", job.organization_id, job.conversation_id, attempts=1
        ).to_json(),
    )

    monkeypatch.setattr(queue, "redis_list", AsyncMock(return_value=[raw]))
    move.reset_mock()
    assert await queue.recover_inflight_jobs() == 1
    move.assert_awaited_once_with(queue.PROCESSING_QUEUE_KEY, queue.JOB_QUEUE_KEY, raw, front=True)


@pytest.mark.asyncio
async def test_requeue_drops_a_job_after_the_attempt_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job("enrich_conversation", uuid.uuid4(), uuid.uuid4(), attempts=queue.MAX_JOB_ATTEMPTS)
    remove = AsyncMock(return_value=1)
    monkeypatch.setattr(queue, "redis_remove", remove)
    move = AsyncMock()
    monkeypatch.setattr(queue, "redis_move_back", move)

    assert await queue.requeue_job(job) is True
    remove.assert_awaited_once_with(queue.PROCESSING_QUEUE_KEY, job.to_json())
    move.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_cleanup_survives_redis_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker

    disposed = AsyncMock()
    stop = asyncio.Event()
    stop.set()
    monkeypatch.setattr(worker, "recover_inflight_jobs", AsyncMock())
    monkeypatch.setattr(worker, "close_redis", AsyncMock(side_effect=RuntimeError("close failed")))
    monkeypatch.setattr(worker, "dispose_engine", disposed)
    await worker.run_worker(stop)
    disposed.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_requeues_failed_job_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker

    job = Job("enrich_conversation", uuid.uuid4(), uuid.uuid4())
    stop = asyncio.Event()
    calls = 0

    async def dequeue(*, timeout: int = 1) -> Job | None:
        del timeout
        nonlocal calls
        calls += 1
        if calls == 1:
            return job
        stop.set()
        return None

    handle = AsyncMock(side_effect=[RuntimeError("temporary"), None])
    monkeypatch.setattr(worker, "dequeue_job", dequeue)
    monkeypatch.setattr(worker, "handle_job", handle)
    monkeypatch.setattr(worker, "recover_inflight_jobs", AsyncMock())
    requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "requeue_job", requeue)
    monkeypatch.setattr(worker, "acknowledge_job", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "close_redis", AsyncMock())
    monkeypatch.setattr(worker, "dispose_engine", AsyncMock())

    await worker.run_worker(stop)
    handle.assert_awaited_once_with(job)
    requeue.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_worker_requeues_when_acknowledgement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker

    job = Job("enrich_conversation", uuid.uuid4(), uuid.uuid4())
    stop = asyncio.Event()
    calls = 0

    async def dequeue(*, timeout: int = 1) -> Job | None:
        nonlocal calls
        del timeout
        calls += 1
        if calls == 1:
            return job
        stop.set()
        return None

    acknowledge = AsyncMock(return_value=False)
    requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "dequeue_job", dequeue)
    monkeypatch.setattr(worker, "handle_job", AsyncMock())
    monkeypatch.setattr(worker, "recover_inflight_jobs", AsyncMock())
    monkeypatch.setattr(worker, "acknowledge_job", acknowledge)
    monkeypatch.setattr(worker, "requeue_job", requeue)
    monkeypatch.setattr(worker, "close_redis", AsyncMock())
    monkeypatch.setattr(worker, "dispose_engine", AsyncMock())
    await worker.run_worker(stop)
    requeue.assert_awaited_once_with(job)
