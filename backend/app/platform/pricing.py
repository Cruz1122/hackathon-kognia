"""Model prices used to estimate call cost in dev mode.

Resolution order for each model:

1. OpenRouter's public catalogue (`GET /api/v1/models`, no key needed), whose
   `pricing.prompt` / `pricing.completion` are USD per token. Cached in Redis so
   the demo stays fast and survives a flaky connection.
2. The hardcoded list rates below (USD per million tokens, captured 2026-10-04)
   as an offline fallback.

Prices are an estimate: they do not model cached input or long-context tiers,
and OpenRouter's rate can differ slightly from a direct provider rate. Unknown
models return ``None`` so callers can show "no price".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import get_pricing_cache_ttl_seconds, get_pricing_source_url
from .redis import redis_get, redis_set

logger = logging.getLogger("hackathon.pricing")

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
CACHE_KEY = "pricing:openrouter:models"
TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


# USD per million tokens. Fallback when OpenRouter is unreachable.
FALLBACK_PRICES: dict[str, ModelPrice] = {
    # OpenAI list prices (developers.openai.com/api/docs/pricing).
    "gpt-6-astra": ModelPrice(10.0, 50.0, 1.0),
    "gpt-6.1-sol": ModelPrice(2.0, 10.0, 0.2),
    "gpt-6-luna": ModelPrice(0.1, 0.5, 0.01),
    "gpt-5.6-sol": ModelPrice(4.0, 20.0, 0.4),
    "gpt-5.6-terra": ModelPrice(2.0, 12.0, 0.2),
    "gpt-5.6-luna": ModelPrice(0.2, 1.2, 0.02),
    "gpt-5.5": ModelPrice(5.0, 30.0, 0.5),
    "gpt-5.5-pro": ModelPrice(30.0, 180.0, 3.0),
    "gpt-5.4": ModelPrice(2.5, 15.0, 0.25),
    "gpt-5.4-mini": ModelPrice(0.75, 3.75, 0.075),
    "gpt-5.4-nano": ModelPrice(0.2, 1.25, 0.02),
    "gpt-4.1": ModelPrice(2.0, 8.0, 0.5),
    "gpt-4.1-mini": ModelPrice(0.4, 1.6, 0.1),
    "gpt-4.1-nano": ModelPrice(0.1, 0.4, 0.025),
    "gpt-4o-mini": ModelPrice(0.15, 0.60, 0.075),
    # Google Gemini (standard tier; the 3.6+ intro discount is not modelled).
    "gemini-3.8-flash": ModelPrice(1.5, 7.5),
    "gemini-3.7-flash": ModelPrice(1.5, 7.5),
    "gemini-3.6-flash": ModelPrice(1.5, 7.5),
    "gemini-3.5-flash": ModelPrice(0.54, 4.5),
    "gemini-3.5-flash-lite": ModelPrice(0.15, 0.60),
    "gemini-3.1-pro-preview": ModelPrice(2.0, 12.0),
    "gemini-3.1-flash-lite": ModelPrice(0.30, 2.50),
    "gemini-2.5-pro": ModelPrice(1.25, 10.0),
    "gemini-2.5-flash": ModelPrice(0.30, 2.50),
    "gemini-2.5-flash-lite": ModelPrice(0.10, 0.40),
    # TypeSafe Jev: input tokens only, output is free.
    "jev-1.13.0": ModelPrice(0.042, 0.0),
    "jev-latest": ModelPrice(0.042, 0.0),
    "jev-preview": ModelPrice(0.042, 0.0),
    "minimax/minimax-m2.7": ModelPrice(0.30, 1.20, 0.06),
    "llama-3.3-70b-versatile": ModelPrice(0.59, 0.79),
}

# ElevenLabs plan: USD 22 per month for 121k credits (1 credit = 1 character).
ELEVENLABS_USD_PER_CHARACTER = 22 / 121_000

# Aliases for IDs that differ between our config and OpenRouter.
MODEL_ALIASES: dict[str, str] = {
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-5.6-luna": "openai/gpt-5.6-luna",
    "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
    "minimax/minimax-m2.7": "minimax/minimax-m2.7",
    "llama-3.3-70b-versatile": "meta-llama/llama-3.3-70b-versatile",
}

_remote_cache: _RemoteCache | None = None


def _candidates(model: str) -> list[str]:
    key = model.strip().lower()
    aliases = [key]
    alias = MODEL_ALIASES.get(key)
    if alias:
        aliases.append(alias)
    # Also try the bare name (last path segment) and any "provider/name".
    if "/" not in key:
        aliases.extend(f"{provider}/{key}" for provider in ("openai", "google", "minimax", "meta-llama", "groq"))
    return list(dict.fromkeys(aliases))


def fallback_price_for(model: str | None) -> ModelPrice | None:
    if not model:
        return None
    key = model.strip().lower()
    if key in FALLBACK_PRICES:
        return FALLBACK_PRICES[key]
    for candidate in _candidates(key):
        for name, price in FALLBACK_PRICES.items():
            if name in candidate or candidate in name:
                return price
    return None


class _RemoteCache:
    """In-memory mirror of the Redis-backed OpenRouter catalogue."""

    def __init__(self) -> None:
        self.prices: dict[str, ModelPrice] = {}
        self.loaded = False

    def lookup(self, model: str) -> ModelPrice | None:
        for candidate in _candidates(model):
            if candidate in self.prices:
                return self.prices[candidate]
        return None


def _parse_catalogue(payload: Any) -> dict[str, ModelPrice]:
    prices: dict[str, ModelPrice] = {}
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return prices
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip().lower()
        pricing = item.get("pricing")
        if not model_id or not isinstance(pricing, dict):
            continue
        try:
            # OpenRouter quotes USD per token; convert to per million.
            input_per_token = float(pricing.get("prompt") or 0)
            output_per_token = float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            continue
        if input_per_token <= 0 and output_per_token <= 0:
            continue
        cached = None
        try:
            cached_value = float(pricing.get("input_cache_read") or 0)
            if cached_value > 0:
                cached = cached_value * 1_000_000
        except (TypeError, ValueError):
            cached = None
        prices[model_id] = ModelPrice(
            input_per_token * 1_000_000,
            output_per_token * 1_000_000,
            cached,
        )
    return prices


def _cached_snapshot() -> _RemoteCache | None:
    return _remote_cache


async def refresh_prices(force: bool = False) -> _RemoteCache:
    """Load the catalogue from Redis (then OpenRouter) into the in-memory cache."""
    global _remote_cache
    if _remote_cache is not None and _remote_cache.loaded and not force:
        return _remote_cache

    cached = await _read_redis_cache()
    if cached is not None and cached.loaded and not force:
        _remote_cache = cached
        return cached

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS)) as client:
            response = await client.get(get_pricing_source_url())
            response.raise_for_status()
            prices = _parse_catalogue(response.json())
    except Exception:
        logger.warning("OpenRouter pricing fetch failed; using fallback prices")
        prices = {}

    if prices:
        remote = _RemoteCache()
        remote.prices = prices
        remote.loaded = True
        _remote_cache = remote
        await _write_redis_cache(prices)
        return remote

    # Nothing fetched: keep any stale in-memory copy, else mark fallback-only.
    if _remote_cache is None:
        _remote_cache = _RemoteCache()
    return _remote_cache


async def _read_redis_cache() -> _RemoteCache | None:
    try:
        raw = await redis_get(CACHE_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    prices = _parse_catalogue({"data": payload.get("data") if isinstance(payload, dict) else None})
    if not prices:
        return None
    remote = _RemoteCache()
    remote.prices = prices
    remote.loaded = True
    return remote


async def _write_redis_cache(prices: dict[str, ModelPrice]) -> None:
    serializable = {
        "data": [
            {
                "id": model_id,
                "pricing": {
                    "prompt": price.input_per_million / 1_000_000,
                    "completion": price.output_per_million / 1_000_000,
                    **({"input_cache_read": price.cached_input_per_million / 1_000_000} if price.cached_input_per_million else {}),
                },
            }
            for model_id, price in prices.items()
        ]
    }
    try:
        await redis_set(CACHE_KEY, json.dumps(serializable), ttl=get_pricing_cache_ttl_seconds())
    except Exception:
        logger.warning("Pricing Redis cache write failed")


def price_for(model: str | None) -> ModelPrice | None:
    """Synchronous lookup: in-memory/Redis catalogue first, then hardcoded fallback."""
    if _remote_cache is not None and _remote_cache.loaded:
        price = _remote_cache.lookup(model) if model else None
        if price is not None:
            return price
    return fallback_price_for(model)


def estimate_cost_usd(
    model: str | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    cost = (
        max(0, int(prompt_tokens)) / 1_000_000 * price.input_per_million
        + max(0, int(completion_tokens)) / 1_000_000 * price.output_per_million
    )
    return round(cost, 6)


def pricing_source() -> str:
    """Where the current prices came from, for the dev UI."""
    if _remote_cache is not None and _remote_cache.loaded:
        return "openrouter"
    return "fallback"


def reset_remote_cache() -> None:
    """Test helper: drop the in-memory catalogue."""
    global _remote_cache
    _remote_cache = None


__all__ = [
    "CACHE_KEY",
    "ELEVENLABS_USD_PER_CHARACTER",
    "FALLBACK_PRICES",
    "ModelPrice",
    "estimate_cost_usd",
    "fallback_price_for",
    "price_for",
    "pricing_source",
    "refresh_prices",
    "reset_remote_cache",
]
