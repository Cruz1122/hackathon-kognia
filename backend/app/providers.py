from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import ModelConfig, Provider


class ProviderError(RuntimeError):
    """An upstream provider failed without exposing its response body."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _sse_data(line: str) -> str | None:
    if line.startswith("data:"):
        return line[5:].strip()
    return None


def _openai_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ProviderError("Provider returned an invalid response")
    choices = payload.get("choices") or []
    if not choices:
        return ""
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise ProviderError("Provider returned an invalid response")
    delta = choices[0].get("delta")
    if delta is None:
        delta = {}
    if not isinstance(delta, dict):
        raise ProviderError("Provider returned an invalid response")
    content = delta.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ProviderError("Provider returned an invalid response")
    return content


def _gemini_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ProviderError("Provider returned an invalid response")
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    if not isinstance(candidates, list) or not isinstance(candidates[0], dict):
        raise ProviderError("Provider returned an invalid response")
    content = candidates[0].get("content")
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise ProviderError("Provider returned an invalid response")
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        raise ProviderError("Provider returned an invalid response")
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("text", ""), str):
            raise ProviderError("Provider returned an invalid response")
        text_parts.append(part.get("text", ""))
    return "".join(text_parts)


async def _stream_sse(response: httpx.Response, parser: Any) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        data = _sse_data(line)
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProviderError("Provider returned malformed streaming data") from exc
        text = parser(payload)
        if text:
            yield text


async def stream_provider(
    config: ModelConfig,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    try:
        if not config.api_key:
            raise ProviderError(f"Missing API key for {config.provider.value}")
        if config.provider is Provider.GEMINI:
            url = f"{config.base_url}/models/{config.model}:streamGenerateContent"
            params = {"alt": "sse"}
            source_messages = messages or [{"role": "user", "content": prompt}]
            body = {
                "contents": [
                    {
                        "role": "model" if message["role"] == "assistant" else "user",
                        "parts": [{"text": message["content"]}],
                    }
                    for message in source_messages
                ]
            }
            headers = {"Content-Type": "application/json", "x-goog-api-key": config.api_key}
            parser = _gemini_text
        else:
            url = f"{config.base_url}/chat/completions"
            params = None
            body = {
                "model": config.model,
                "messages": messages or [{"role": "user", "content": prompt}],
                "stream": True,
            }
            headers = {"Authorization": f"Bearer {config.api_key}"}
            parser = _openai_text

        try:
            async with http_client.stream(
                "POST", url, params=params, headers=headers, json=body
            ) as response:
                if response.status_code >= 400:
                    retryable = response.status_code not in {400, 401, 403, 404}
                    raise ProviderError(
                        f"{config.provider.value} returned HTTP {response.status_code}",
                        retryable=retryable,
                    )
                async for text in _stream_sse(response, parser):
                    yield text
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ProviderError(f"{config.provider.value} request failed") from exc
    finally:
        if owns_client:
            await http_client.aclose()
