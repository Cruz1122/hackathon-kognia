from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from ..config import ModelConfig, Provider
from ..features.agent.tools import (
    AGENT_SYSTEM,
    CanonicalTool,
    to_gemini_tools,
    to_openai_tools,
)
from .contracts import LLMCapabilities
from .errors import ProviderError


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


def _with_system(messages: Sequence[dict[str, Any]] | None, prompt: str) -> list[dict[str, Any]]:
    source = list(messages) if messages else [{"role": "user", "content": prompt}]
    extras: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in source:
        if message.get("role") == "system":
            content = str(message.get("content") or "").strip()
            if content and content != AGENT_SYSTEM:
                extras.append(content)
            continue
        rest.append(message)
    system = AGENT_SYSTEM if not extras else AGENT_SYSTEM + "\n\n" + "\n\n".join(extras)
    return [{"role": "system", "content": system}, *rest]


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


async def _post_stream(
    config: ModelConfig,
    prompt: str,
    *,
    client: httpx.AsyncClient | None,
    messages: Sequence[dict[str, Any]] | None,
    tools: Sequence[CanonicalTool] | None,
    openai_compatible: bool,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    try:
        if not config.api_key:
            raise ProviderError(f"Missing API key for {config.provider.value}", retryable=False)
        source_messages = _with_system(messages, prompt)
        if openai_compatible:
            url = f"{config.base_url}/chat/completions"
            params = None
            body: dict[str, Any] = {
                "model": config.model,
                "messages": source_messages,
                "stream": True,
            }
            if tools:
                body["tools"] = to_openai_tools(tools)
                body["tool_choice"] = "auto"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            stream_events = _stream_openai_chat
        else:
            url = f"{config.base_url}/models/{config.model}:streamGenerateContent"
            params = {"alt": "sse"}
            system_text = str(source_messages[0].get("content") or AGENT_SYSTEM)
            body = {
                "systemInstruction": {"parts": [{"text": system_text}]},
                "contents": _gemini_contents(source_messages),
            }
            if tools:
                body["tools"] = to_gemini_tools(tools)
            headers = {"Content-Type": "application/json", "x-goog-api-key": config.api_key}
            stream_events = _stream_gemini_chat

        # Reasoning models (for example gpt-6-luna) reject function tools on
        # /chat/completions unless reasoning is explicitly disabled. The API
        # names the fix in the 400 body, so retry once with reasoning_effort
        # instead of keeping a per-model capability list. Non-reasoning models
        # never receive the argument because their first request succeeds.
        reasoned_tools_retry = False
        try:
            while True:
                async with http_client.stream(
                    "POST", url, params=params, headers=headers, json=body
                ) as response:
                    if (response.status_code == 400 and openai_compatible and tools
                            and not reasoned_tools_retry):
                        detail = (await response.aread()).decode("utf-8", "ignore")
                        if "reasoning_effort" in detail:
                            body["reasoning_effort"] = "none"
                            reasoned_tools_retry = True
                            continue
                    if response.status_code >= 400:
                        retryable = response.status_code not in {400, 401, 403, 404}
                        raise ProviderError(
                            f"{config.provider.value} returned HTTP {response.status_code}",
                            retryable=retryable,
                        )
                    async for event in stream_events(response):
                        yield event
                    return
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ProviderError(f"{config.provider.value} request failed") from exc
    finally:
        if owns_client:
            await http_client.aclose()


class OpenAICompatibleLLM:
    capabilities = LLMCapabilities(supports_tools=True, supports_streaming=True)

    async def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        async for event in _post_stream(
            config,
            prompt,
            client=client,
            messages=messages,
            tools=tools,
            openai_compatible=True,
        ):
            yield event


class GeminiLLM:
    capabilities = LLMCapabilities(supports_tools=True, supports_streaming=True)

    async def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        async for event in _post_stream(
            config,
            prompt,
            client=client,
            messages=messages,
            tools=tools,
            openai_compatible=False,
        ):
            yield event


class RoutedLLM:
    """Pick the vendor adapter from ModelConfig.provider. Capabilities stay explicit."""

    capabilities = LLMCapabilities(supports_tools=True, supports_streaming=True)

    def __init__(
        self,
        *,
        openai: OpenAICompatibleLLM | None = None,
        gemini: GeminiLLM | None = None,
    ) -> None:
        self._openai = openai or OpenAICompatibleLLM()
        self._gemini = gemini or GeminiLLM()

    def _adapter_for(self, config: ModelConfig) -> OpenAICompatibleLLM | GeminiLLM:
        if config.provider is Provider.GEMINI:
            return self._gemini
        return self._openai

    async def stream(
        self,
        config: ModelConfig,
        prompt: str,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[CanonicalTool] | None = None,
        client: Any | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        adapter = self._adapter_for(config)
        async for event in adapter.stream(
            config, prompt, messages=messages, tools=tools, client=client
        ):
            yield event


async def stream_chat(
    config: ModelConfig,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    messages: list[dict[str, Any]] | None = None,
    *,
    tools: Sequence[CanonicalTool] | bool | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    from ..features.agent.tools import CANONICAL_TOOLS

    selected: Sequence[CanonicalTool] | None
    if tools is True:
        selected = CANONICAL_TOOLS
    elif tools is False or tools is None:
        selected = None
    else:
        selected = tools
    adapter = GeminiLLM() if config.provider is Provider.GEMINI else OpenAICompatibleLLM()
    async for event in adapter.stream(
        config, prompt, messages=messages, tools=selected, client=client
    ):
        yield event


async def stream_provider(
    config: ModelConfig,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    async for kind, payload in stream_chat(config, prompt, client=client, messages=messages):
        if kind == "token":
            yield str(payload["text"])
