"""Minimal resilient worker process: ``python -m app.worker``."""

from __future__ import annotations

import asyncio
import logging

from .db.session import dispose_engine, get_session_factory
from .platform.queue import acknowledge_job, dequeue_job, recover_inflight_jobs, requeue_job
from .platform.redis import close_redis

logger = logging.getLogger("hackathon.worker")


async def handle_job(job: object) -> None:
    from .enrichment.service import enrich_conversation

    await enrich_conversation(get_session_factory(), job)  # type: ignore[arg-type]


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    stop_event = stop_event or asyncio.Event()
    try:
        try:
            await recover_inflight_jobs()
        except Exception:
            logger.exception("Could not recover in-flight jobs")
        while not stop_event.is_set():
            job = None
            try:
                job = await dequeue_job(timeout=1)
                if job is not None:
                    await handle_job(job)
                    if not await acknowledge_job(job):
                        logger.warning("Job acknowledgement failed: %s", job.conversation_id)
                        if not await requeue_job(job):
                            logger.error("Unacknowledged job could not be requeued: %s", job.conversation_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Job failed; worker continues")
                if job is not None and not await requeue_job(job):
                    logger.error("Failed job could not be requeued: %s", job.conversation_id)
                await asyncio.sleep(0.25)
    finally:
        try:
            try:
                await close_redis()
            except Exception:
                logger.exception("Redis cleanup failed")
        finally:
            await dispose_engine()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
