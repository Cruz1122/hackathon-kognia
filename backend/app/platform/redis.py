"""Small optional Redis adapter shared by analytics and the worker queue."""

from __future__ import annotations

import os
from typing import Any

from redis.asyncio import Redis

from ..config import get_redis_url

_client: Redis[str] | None = None


def _enabled() -> bool:
    value = os.getenv("REDIS_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on"} or os.getenv("RUN_REDIS_INTEGRATION") == "1"


def get_redis_client() -> Redis[str]:
    if not _enabled():
        raise RuntimeError("Redis is disabled; use SODA3 as the live IPS data source")
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_redis_url(),
            decode_responses=True,
            socket_connect_timeout=1,
            # BLMOVE uses a one-second blocking timeout. Keep the socket
            # timeout above it so an empty poll is a normal nil result.
            socket_timeout=2,
        )
    return _client


async def ping_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def redis_get(key: str) -> str | None:
    return await get_redis_client().get(key)


async def redis_set(key: str, value: str, *, ttl: int) -> bool:
    return bool(await get_redis_client().set(key, value, ex=ttl))


async def redis_incr(key: str) -> int:
    return int(await get_redis_client().incr(key))


async def redis_push(key: str, value: str) -> int:
    return int(await get_redis_client().rpush(key, value))


async def redis_pop(key: str, *, timeout: int = 1) -> tuple[str, str] | None:
    item = await get_redis_client().blpop(key, timeout=timeout)
    if item is None:
        return None
    return str(item[0]), str(item[1])


async def redis_claim(source: str, processing: str, *, timeout: int = 1) -> str | None:
    value = await get_redis_client().blmove(source, processing, timeout, src="LEFT", dest="RIGHT")
    return str(value) if value is not None else None


async def redis_remove(key: str, value: str) -> int:
    return int(await get_redis_client().lrem(key, 1, value))


async def redis_left_pop(key: str) -> str | None:
    value = await get_redis_client().lpop(key)
    return str(value) if value is not None else None


async def redis_list(key: str) -> list[str]:
    return [str(item) for item in await get_redis_client().lrange(key, 0, -1)]


async def redis_move_back(
    processing: str,
    destination: str,
    value: str,
    *,
    front: bool = False,
    replacement: str | None = None,
) -> bool:
    """Atomically move a claimed job back without a loss window between commands."""
    script = (
        "local removed = redis.call('LREM', KEYS[1], 1, ARGV[1]) "
        "if removed > 0 then redis.call(ARGV[2], KEYS[2], ARGV[3]) end "
        "return removed"
    )
    push_command = "LPUSH" if front else "RPUSH"
    result = await get_redis_client().eval(
        script,
        2,
        processing,
        destination,
        value,
        push_command,
        replacement if replacement is not None else value,
    )
    return int(result or 0) > 0


__all__ = [
    "close_redis",
    "get_redis_client",
    "ping_redis",
    "redis_get",
    "redis_incr",
    "redis_claim",
    "redis_left_pop",
    "redis_list",
    "redis_move_back",
    "redis_pop",
    "redis_push",
    "redis_remove",
    "redis_set",
]
