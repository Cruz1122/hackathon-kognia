from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import ModelConfig, Provider
from .features.agent.tools import AGENT_SYSTEM, GEMINI_TOOLS, OPENAI_TOOLS


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


def _gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.get("name") or "tool",
                                "response": {"result": message.get("content") or ""},
                            }
                        }
                    ],
                }
            )
            continue
        if message.get("tool_calls"):
            parts = []
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                raw_args = function.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                parts.append({"functionCall": {"name": function.get("name") or "tool", "args": args}})
            contents.append({"role": "model", "parts": parts})
            continue
        contents.append(
            {
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": message.get("content") or ""}],
            }
        )
    return contents


def _with_system(messages: list[dict[str, Any]] | None, prompt: str) -> list[dict[str, Any]]:
    source = list(messages) if messages else [{"role": "user", "content": prompt}]
    if not source or source[0].get("role") != "system":
        return [{"role": "system", "content": AGENT_SYSTEM}, *source]
    return source


async def _stream_openai_chat(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    calls: dict[int, dict[str, str]] = {}
    async for line in response.aiter_lines():
        data = _sse_data(line)
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProviderError("Provider returned malformed streaming data") from exc
        text = _openai_text(payload)
        if text:
            yield "token", {"text": text}
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        delta = choices[0].get("delta") or {}
        if not isinstance(delta, dict):
            continue
        for tool_call in delta.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            index = int(tool_call.get("index") or 0)
            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if isinstance(tool_call.get("id"), str) and tool_call["id"]:
                slot["id"] = tool_call["id"]
            function = tool_call.get("function") or {}
            if isinstance(function, dict):
                if isinstance(function.get("name"), str) and function["name"]:
                    slot["name"] = function["name"]
                if isinstance(function.get("arguments"), str):
                    slot["arguments"] += function["arguments"]
    if calls:
        yield "tool_calls", {"calls": [calls[index] for index in sorted(calls)]}


async def _stream_gemini_chat(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    calls: list[dict[str, str]] = []
    async for line in response.aiter_lines():
        data = _sse_data(line)
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProviderError("Provider returned malformed streaming data") from exc
        text = _gemini_text(payload)
        if text:
            yield "token", {"text": text}
        candidates = payload.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            continue
        content = candidates[0].get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall")
            if not isinstance(call, dict):
                continue
            name = call.get("name") if isinstance(call.get("name"), str) else "tool"
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            calls.append({"id": f"call_{len(calls) + 1}", "name": name, "arguments": json.dumps(args)})
    if calls:
        yield "tool_calls", {"calls": calls}


async def stream_chat(
    config: ModelConfig,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    messages: list[dict[str, Any]] | None = None,
    *,
    tools: bool = False,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    try:
        if not config.api_key:
            raise ProviderError(f"Missing API key for {config.provider.value}")
        source_messages = _with_system(messages, prompt)
        if config.provider is Provider.GEMINI:
            url = f"{config.base_url}/models/{config.model}:streamGenerateContent"
            params = {"alt": "sse"}
            body: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": AGENT_SYSTEM}]},
                "contents": _gemini_contents(source_messages),
            }
            if tools:
                body["tools"] = GEMINI_TOOLS
            headers = {"Content-Type": "application/json", "x-goog-api-key": config.api_key}
            stream_events = _stream_gemini_chat
        else:
            url = f"{config.base_url}/chat/completions"
            params = None
            body = {
                "model": config.model,
                "messages": source_messages,
                "stream": True,
            }
            if tools:
                body["tools"] = OPENAI_TOOLS
                body["tool_choice"] = "auto"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            stream_events = _stream_openai_chat

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
                async for event in stream_events(response):
                    yield event
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ProviderError(f"{config.provider.value} request failed") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def stream_provider(
    config: ModelConfig,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    async for kind, payload in stream_chat(config, prompt, client=client, messages=messages):
        if kind == "token":
            yield str(payload["text"])
