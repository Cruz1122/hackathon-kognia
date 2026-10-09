from __future__ import annotations

import pytest

from app.ips_soda3.cache import InMemoryCache
from app.platform import redis as redis_module


@pytest.mark.asyncio
async def test_soda3_cache_is_process_local() -> None:
    cache = InMemoryCache()
    key = cache.key_for({"where": "municipio = 'Bogotá'"})

    await cache.set(key, {"rows": [{"id": "1"}], "fetched_at": "now"}, ttl=60)

    assert await cache.get(key) == {"rows": [{"id": "1"}], "fetched_at": "now"}


def test_redis_is_disabled_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_ENABLED", "false")
    monkeypatch.delenv("RUN_REDIS_INTEGRATION", raising=False)

    with pytest.raises(RuntimeError, match="Redis is disabled"):
        redis_module.get_redis_client()
