from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from ...config import AppEnv, Provider, get_app_env, get_model_chain
from ...providers import ProviderError, stream_chat, stream_provider
from .tools import describe_tool_done, describe_tool_start, execute_tool, parse_arguments

Message = dict[str, Any]
ProviderStream = Callable[..., AsyncIterator[str]]
MAX_TOOL_ROUNDS = 4


async def stream_agent(
    prompt: str,
    *,
    messages: Sequence[Message] | None = None,
    provider_stream: ProviderStream,
) -> AsyncIterator[tuple[str, dict[str, str]]]:
    """Stream the existing provider chain without duplicating agent behavior."""
    chain = get_model_chain()
    attempts_per_model = 3
    total_attempts = len(chain) * attempts_per_model
    environment = get_app_env()
    configs = (
        [chain[index % len(chain)] for index in range(total_attempts)]
        if environment is AppEnv.TEST
        else [config for config in chain for _ in range(attempts_per_model)]
    )

    attempt = 0
    permanent_failures: set[Provider] = set()
    use_tools = provider_stream is stream_provider
    while attempt < len(configs):
        config = configs[attempt]
        attempt += 1
        if config.provider in permanent_failures:
            continue
        emitted_tokens = False
        try:
            if use_tools:
                conversation: list[Message] = [*(messages or []), {"role": "user", "content": prompt}]
                for _ in range(MAX_TOOL_ROUNDS):
                    tool_calls: list[dict[str, str]] = []
                    async for kind, payload in stream_chat(
                        config,
                        prompt,
                        messages=conversation,
                        tools=True,
                    ):
                        if kind == "token":
                            emitted_tokens = True
                            yield "token", {"text": str(payload["text"])}
                        elif kind == "tool_calls":
                            tool_calls = list(payload.get("calls") or [])
                    if not tool_calls:
                        break
                    assistant_calls = []
                    tool_messages: list[Message] = []
                    for index, call in enumerate(tool_calls):
                        call_id = call.get("id") or f"call_{index + 1}"
                        name = call.get("name") or "tool"
                        raw_arguments = call.get("arguments") or "{}"
                        arguments = parse_arguments(raw_arguments)
                        title, status = describe_tool_start(name, arguments)
                        yield "tool.started", {"tool": name, "title": title, "status": status}
                        try:
                            result = execute_tool(name, arguments)
                        except (TypeError, ValueError) as exc:
                            result = str(exc)
                        done_title, done_status = describe_tool_done(name, arguments, result)
                        yield "tool.completed", {
                            "tool": name,
                            "title": done_title,
                            "status": done_status,
                            "result": result,
                        }
                        assistant_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": raw_arguments},
                            }
                        )
                        tool_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": name,
                                "content": result,
                            }
                        )
                    conversation.append(
                        {"role": "assistant", "content": None, "tool_calls": assistant_calls}
                    )
                    conversation.extend(tool_messages)
                if not emitted_tokens:
                    raise ProviderError("Provider returned an empty stream")
                yield "done", {"provider": config.provider.value, "model": config.model}
                return

            if messages is None:
                stream = provider_stream(config, prompt)
            else:
                conversation = [*messages, {"role": "user", "content": prompt}]
                stream = provider_stream(config, prompt, messages=conversation)
            async for token in stream:
                emitted_tokens = True
                yield "token", {"text": token}
            if not emitted_tokens:
                raise ProviderError("Provider returned an empty stream")
            yield "done", {"provider": config.provider.value, "model": config.model}
            return
        except ProviderError as exc:
            if emitted_tokens:
                yield "error", {"message": "La respuesta del proveedor se interrumpió"}
                return
            if not exc.retryable:
                permanent_failures.add(config.provider)
            if attempt >= total_attempts:
                yield "error", {"message": "No hay proveedores disponibles"}
                return

    yield "error", {"message": "No hay proveedores disponibles"}
