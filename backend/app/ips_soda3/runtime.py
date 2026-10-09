"""Lifecycle-managed SODA3 adapter runtime for the existing FastAPI app."""

from __future__ import annotations

import httpx

from ..platform.redis import get_redis_client
from .cache import RedisCache
from .client import Soda3Client
from .config import IPSSettings
from .service import IPSService


class IPSConfigurationError(RuntimeError):
    """Raised when the adapter was not configured at backend startup."""


class IPSRuntime:
    def __init__(self) -> None:
        self.service: IPSService | None = None
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> IPSService:
        await self.stop()
        settings = IPSSettings.from_env()
        http = httpx.AsyncClient()
        try:
            client = Soda3Client(
                app_token=settings.app_token,
                http=http,
                timeout=settings.http_timeout,
                max_attempts=settings.max_attempts,
            )
            service = IPSService(
                client,
                RedisCache(get_redis_client()),
                ttl=settings.cache_ttl,
                empty_ttl=settings.empty_ttl,
                stale_ttl=settings.stale_ttl,
            )
        except Exception:
            await http.aclose()
            raise
        self._http = http
        self.service = service
        return service

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self.service = None

    def require_service(self) -> IPSService:
        if self.service is None:
            raise IPSConfigurationError("La integración SODA3 IPS no está configurada.")
        return self.service


runtime = IPSRuntime()


async def start_ips_runtime() -> IPSService:
    return await runtime.start()


async def stop_ips_runtime() -> None:
    await runtime.stop()


def get_ips_service() -> IPSService:
    return runtime.require_service()


__all__ = [
    "IPSConfigurationError",
    "IPSRuntime",
    "get_ips_service",
    "runtime",
    "start_ips_runtime",
    "stop_ips_runtime",
]
