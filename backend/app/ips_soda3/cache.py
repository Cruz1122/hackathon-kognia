"""Best-effort JSON cache-aside for the IPS SODA3 adapter."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class InMemoryCache:
    """Process-local cache for the live SODA3 adapter.

    Production deliberately does not use Redis: SODA3 remains the only
    external source for IPS data, and a restart simply repopulates this cache.
    """

    def __init__(self, *, namespace: str = "ips:soda3:v1") -> None:
        self._namespace = namespace
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}

    def key_for(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"dataset": "s2ru-bqt6", "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"

    async def get(self, key: str) -> dict[str, Any] | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: dict[str, Any], *, ttl: int) -> None:
        self._values[key] = (time.monotonic() + ttl, value)


class RedisCache:
    """Cache SODA3 rows without making Redis a source of truth."""

    def __init__(self, redis: Any, *, namespace: str = "ips:soda3:v1") -> None:
        self._redis = redis
        self._namespace = namespace

    def key_for(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"dataset": "s2ru-bqt6", "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            parsed = json.loads(value)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("rows"), list):
                return None
            return parsed
        except Exception as exc:
            logger.warning("Redis GET falló: %s", type(exc).__name__)
            return None

    async def set(self, key: str, value: dict[str, Any], *, ttl: int) -> None:
        try:
            await self._redis.set(
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                ex=ttl,
            )
        except Exception as exc:
            logger.warning("Redis SET falló: %s", type(exc).__name__)


__all__ = ["RedisCache"]
