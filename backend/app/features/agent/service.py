from collections.abc import AsyncIterator, Callable, Sequence

from ...config import AppEnv, Provider, get_app_env, get_model_chain
from ...providers import ProviderError

Message = dict[str, str]
ProviderStream = Callable[..., AsyncIterator[str]]


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
    while attempt < len(configs):
        config = configs[attempt]
        attempt += 1
        if config.provider in permanent_failures:
            continue
        emitted_tokens = False
        try:
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
