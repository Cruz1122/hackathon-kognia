"""Environment-backed configuration for the IPS SODA3 adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_REDIS_URL = "redis://localhost:16379/0"
DEFAULT_CACHE_TTL_SECONDS = 3600
DEFAULT_EMPTY_CACHE_TTL_SECONDS = 300
DEFAULT_STALE_CACHE_TTL_SECONDS = 86400
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
DEFAULT_HTTP_MAX_ATTEMPTS = 3
PLACEHOLDER_TOKENS = {"REEMPLAZAR_CON_TOKEN_REAL", "tu_token_soda3"}


def _required_token() -> str:
    """Resolve the user-facing name first, then the official skill alias."""
    token = os.getenv("API_KEY_SODA3", "").strip()
    if not token or token in PLACEHOLDER_TOKENS:
        token = os.getenv("SODA_APP_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "SODA3 no está configurado: define API_KEY_SODA3 "
            "(o SODA_APP_TOKEN) en el entorno del backend."
        )
    return token


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero") from exc
    if value < 1:
        raise ValueError(f"{name} debe ser positivo")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un número") from exc
    if value <= 0:
        raise ValueError(f"{name} debe ser positivo")
    return value


@dataclass(frozen=True, slots=True)
class IPSSettings:
    """Validated settings used to construct one adapter runtime."""

    app_token: str
    redis_url: str = DEFAULT_REDIS_URL
    cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS
    empty_ttl: int = DEFAULT_EMPTY_CACHE_TTL_SECONDS
    stale_ttl: int = DEFAULT_STALE_CACHE_TTL_SECONDS
    http_timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_HTTP_MAX_ATTEMPTS

    @classmethod
    def from_env(cls) -> "IPSSettings":
        settings = cls(
            app_token=_required_token(),
            redis_url=os.getenv("REDIS_URL", DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL,
            cache_ttl=_positive_int("IPS_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
            empty_ttl=_positive_int(
                "IPS_CACHE_EMPTY_TTL_SECONDS", DEFAULT_EMPTY_CACHE_TTL_SECONDS
            ),
            stale_ttl=_positive_int(
                "IPS_CACHE_STALE_TTL_SECONDS", DEFAULT_STALE_CACHE_TTL_SECONDS
            ),
            http_timeout=_positive_float(
                "IPS_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS
            ),
            max_attempts=_positive_int(
                "IPS_HTTP_MAX_ATTEMPTS", DEFAULT_HTTP_MAX_ATTEMPTS
            ),
        )
        if settings.max_attempts > 5:
            raise ValueError("IPS_HTTP_MAX_ATTEMPTS debe estar entre 1 y 5")
        return settings


__all__ = ["IPSSettings"]
