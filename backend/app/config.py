from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class AppEnv(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class Provider(StrEnum):
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    GROQ = "groq"
    OPENAI = "openai"


DEFAULT_REDIS_URL = "redis://localhost:16379/0"
DEFAULT_ANALYTICS_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class ModelConfig:
    provider: Provider
    model: str
    api_key: str
    base_url: str


MODEL_CHAINS: dict[AppEnv, tuple[tuple[Provider, str], ...]] = {
    AppEnv.TEST: (
        (Provider.OPENAI, "gpt-5.4-mini"),
        (Provider.GEMINI, "gemini-3.5-flash-lite"),
        (Provider.OPENROUTER, "minimax/minimax-m2.7"),
        (Provider.GROQ, "llama-3.3-70b-versatile"),
    ),
    AppEnv.PRODUCTION: (
        (Provider.OPENAI, "gpt-6-luna"),
        (Provider.GEMINI, "gemini-3.5-flash-lite"),
    ),
}

API_KEY_ENV: dict[Provider, str] = {
    Provider.GEMINI: "GEMINI_API_KEY",
    Provider.OPENROUTER: "OPENROUTER_API_KEY",
    Provider.GROQ: "GROQ_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
}

PLACEHOLDER_KEYS = {"", "tu_clave_de_openrouter", "tu_clave_de_groq", "tu_clave_de_openai"}

BASE_URLS: dict[Provider, str] = {
    Provider.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
    Provider.OPENROUTER: "https://openrouter.ai/api/v1",
    Provider.GROQ: "https://api.groq.com/openai/v1",
    Provider.OPENAI: "https://api.openai.com/v1",
}


def get_app_env(value: str | None = None) -> AppEnv:
    raw_value = (value or os.getenv("APP_ENV", AppEnv.TEST)).lower()
    try:
        return AppEnv(raw_value)
    except ValueError as exc:
        raise ValueError("APP_ENV must be 'test' or 'production'") from exc


def get_model_chain(app_env: AppEnv | str | None = None) -> tuple[ModelConfig, ...]:
    environment = get_app_env(app_env) if isinstance(app_env, str) or app_env is None else app_env
    return tuple(
        ModelConfig(
            provider=provider,
            model=(os.getenv('OPENAI_MODEL', model).strip() or model) if provider is Provider.OPENAI else model,
            api_key=_api_key_for(provider),
            base_url=BASE_URLS[provider],
        )
        for provider, model in MODEL_CHAINS[environment]
    )


def _api_key_for(provider: Provider) -> str:
    value = os.getenv(API_KEY_ENV[provider], "").strip()
    return "" if value.lower() in PLACEHOLDER_KEYS else value


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL


def get_analytics_cache_ttl_seconds() -> int:
    raw = os.getenv("ANALYTICS_CACHE_TTL_SECONDS", str(DEFAULT_ANALYTICS_CACHE_TTL_SECONDS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ANALYTICS_CACHE_TTL_SECONDS must be an integer") from exc
    if value < 1:
        raise ValueError("ANALYTICS_CACHE_TTL_SECONDS must be positive")
    return value
