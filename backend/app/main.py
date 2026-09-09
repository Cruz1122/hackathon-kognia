import asyncio
import json
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import AppEnv, Provider, get_app_env, get_model_chain
from .features.agent.service import stream_agent
from .features.chat.schemas import AskRequest
from .features.transcription.schemas import TranscriptionResponse
from .features.transcription.service import preload_model, transcribe_audio
from .features.synthesis.schemas import SynthesisRequest
from .features.synthesis.service import synthesize_text
from .providers import ProviderError, stream_provider


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_repository_environment(env_file: Path | None = None) -> None:
    """Load the repository-root environment without overriding shell variables."""
    load_dotenv(env_file or REPOSITORY_ROOT / ".env", override=False)


load_repository_environment()

transcription_lock = asyncio.Semaphore(1)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm Whisper before serving requests without blocking the event loop."""
    try:
        await asyncio.to_thread(preload_model)
    except ImportError:
        # Keep the API available for text-only fallback when dependencies are absent.
        pass
    yield


app = FastAPI(title="Hackathon API", version="0.1.0", lifespan=lifespan)

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:18473")
allowed_frontend_origins = list(
    dict.fromkeys([frontend_origin, "http://localhost:18473", "http://127.0.0.1:18473"])
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_frontend_origins,
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


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(request: Request) -> TranscriptionResponse:
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=422, detail="El audio está vacío.")
    if len(audio) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El audio supera el límite permitido.")
    try:
        async with transcription_lock:
            text = await asyncio.to_thread(
                transcribe_audio,
                audio,
                request.headers.get("content-type", "audio/webm"),
            )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Whisper local no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo transcribir el audio.") from exc
    return TranscriptionResponse(text=text)


@app.post("/synthesize", response_class=Response)
async def synthesize(request: SynthesisRequest) -> Response:
    try:
        audio = await asyncio.to_thread(synthesize_text, request.text)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="espeak-ng no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo sintetizar el texto.") from exc
    return Response(content=audio, media_type="audio/wav")


class ErrorResponse(BaseModel):
    detail: str = Field(description="Descripción segura del error.")


def _event(name: str, payload: dict[str, str]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _ask_stream(
    prompt: str,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    async for name, payload in stream_agent(
        prompt,
        messages=messages,
        provider_stream=stream_provider,
    ):
        yield _event(name, payload)


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

    history = [message.model_dump() for message in request.messages]
    stream = _ask_stream(request.prompt, history or None)
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
