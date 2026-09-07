import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import AppEnv, Provider, get_app_env, get_model_chain
from .providers import ProviderError, stream_provider


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_repository_environment(env_file: Path | None = None) -> None:
    """Load the repository-root environment without overriding shell variables."""
    load_dotenv(env_file or REPOSITORY_ROOT / ".env", override=False)


load_repository_environment()

app = FastAPI(title="Hackathon API", version="0.1.0")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:18473")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/hello")
def hello() -> dict[str, str]:
    return {"message": "FastAPI + Astro funcionando"}


class AskRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=10_000,
        description="Pregunta que se enviará al modelo seleccionado.",
        examples=["Explica qué es el streaming de tokens."],
    )


class ErrorResponse(BaseModel):
    detail: str = Field(description="Descripción segura del error.")


def _event(name: str, payload: dict[str, str]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _ask_stream(prompt: str) -> AsyncIterator[str]:
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
            async for token in stream_provider(config, prompt):
                emitted_tokens = True
                yield _event("token", {"text": token})
            if not emitted_tokens:
                raise ProviderError("Provider returned an empty stream")
            yield _event("done", {"provider": config.provider.value, "model": config.model})
            return
        except ProviderError as exc:
            if emitted_tokens:
                yield _event("error", {"message": "La respuesta del proveedor se interrumpió"})
                return
            if not exc.retryable:
                permanent_failures.add(config.provider)
            if attempt >= total_attempts:
                yield _event("error", {"message": "No hay proveedores disponibles"})
                return

    yield _event("error", {"message": "No hay proveedores disponibles"})


async def _replay_stream(first_chunk: str, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    yield first_chunk
    async for chunk in stream:
        yield chunk


@app.post(
    "/ask",
    response_class=StreamingResponse,
    response_model=None,
    summary="Pregunta a los modelos configurados",
    description=(
        "Devuelve un stream SSE con eventos `token` y `done`. "
        "Los fallos antes del primer token se devuelven como JSON con código HTTP "
        "4xx/5xx; los fallos posteriores se notifican dentro del stream."
    ),
    responses={
        200: {
            "description": "Stream SSE de tokens generado por un provider.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        'event: token\ndata: {"text":"Hola"}\n\n'
                        'event: done\ndata: {"provider":"openai","model":"gpt-5.6-luna"}\n\n'
                    ),
                }
            },
        },
        500: {"model": ErrorResponse, "description": "La configuración del entorno no es válida."},
        502: {"model": ErrorResponse, "description": "Los providers no pudieron responder."},
        503: {"model": ErrorResponse, "description": "No hay API keys configuradas."},
    },
)
async def ask(request: AskRequest) -> Response:
    try:
        chain = get_model_chain()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Configuración de entorno inválida") from exc
    if not any(config.api_key for config in chain):
        return JSONResponse(
            status_code=503,
            content={"detail": "No hay API keys configuradas para el entorno seleccionado."},
        )

    stream = _ask_stream(request.prompt)
    try:
        first_chunk = await anext(stream)
    except StopAsyncIteration:
        return JSONResponse(status_code=502, content={"detail": "Los providers no respondieron."})

    if first_chunk.startswith("event: error"):
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
        return JSONResponse(status_code=502, content={"detail": "Los providers no pudieron responder."})

    return StreamingResponse(_replay_stream(first_chunk, stream), media_type="text/event-stream")
