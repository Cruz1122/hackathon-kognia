import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.dependencies import get_current_user, require_superadmin
from .auth.passwords import DUMMY_PASSWORD_HASH, PasswordValidationError, hash_password, verify_password
from .auth.schemas import (
    AdminCreate,
    LoginRequest,
    LoginResponse,
    OrganizationCreate,
    OrganizationResponse,
    UserResponse,
)
from .auth.tokens import (
    AuthConfigurationError,
    InvalidTokenError,
    authenticate_token,
    create_access_token,
    get_access_token_expire_minutes,
)
from .config import AppEnv, Provider, get_app_env, get_model_chain
from .db.models import Call, CallStatus, Conversation, Message as DbMessage
from .db.models import MessageRole, Organization, User, UserRole
from .db.queries import create_conversation, get_conversation, list_messages
from .db.session import check_database, dispose_engine, get_db
from .platform.redis import close_redis
from .platform.tracing import TraceRecorder, save_trace
from .features.agent.service import context_window, stream_agent
from .agent.tools.contracts import ToolContext
from .features.transcription.service import (
    SpeechSanitizer,
    pcm_speech_features,
    recent_speech_tail,
    recover_short_transcript,
    stt_label,
)
from .features.chat.schemas import (
    AskRequest,
    ConversationCreate,
    ConversationResponse,
)
from .features.transcription.schemas import TranscriptionResponse
from .features.synthesis.chunk import take_semantic_chunk as _take_semantic_chunk
from .features.synthesis.schemas import SynthesisRequest
from .features.synthesis.service import SynthesisNotConfigured, open_speech_turn
from .ips_soda3.api import create_ips_router
from .ips_soda3.runtime import start_ips_runtime, stop_ips_runtime
from .providers import (
    ProviderError,
    llm_provider as default_llm_provider,
    stt_provider as default_stt_provider,
    tts_provider as default_tts_provider,
)
from .providers.contracts import LLMProvider, SpeechToTextProvider, TextToSpeechProvider
from .realtime.events import RealtimeEvent
from .realtime.hub import RealtimeHub
from .platform.rag.runtime import embeddings as rag_embeddings
from .analytics.router import router as analytics_router
from .platform.queue import enqueue_enrichment
from .commercial.router import router as commercial_router
from .features.dev.router import router as dev_router
from .telephony.audio import CANONICAL_RATE, resample_pcm16le, timeline_ms
from .telephony.bridge import (
    agent_heard_ms,
    catch_up_customer_clock,
    mute_agent_from,
    place_customer_pcm,
    remember_agent_pcm,
)
from .telephony.router import router as telnyx_router
from .telephony.runtime import runtime as telephony_runtime
from .telephony.sessions import CallSession
from .telephony.timeline import timeline
from .whatsapp.router import router as whatsapp_router
from .agent.router import router as agent_state_router
from .logging_config import install_access_log_filter


logger = logging.getLogger("hackathon.voice")

# Quiet the high-frequency polling endpoints. Uvicorn configures logging before
# importing this module, so the filter persists for the whole server lifetime.
install_access_log_filter()

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_repository_environment(env_file: Path | None = None) -> None:
    """Load repository and backend environments without overriding shell variables."""
    if env_file is not None:
        load_dotenv(env_file, override=False)
        return
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    load_dotenv(REPOSITORY_ROOT / "backend" / ".env", override=False)


load_repository_environment()

llm_provider: LLMProvider = default_llm_provider
stt_provider: SpeechToTextProvider = default_stt_provider
tts_provider: TextToSpeechProvider = default_tts_provider
transcription_lock = asyncio.Semaphore(1)
realtime_hub = RealtimeHub()
sherpa_status = "starting"
tts_status = "starting"
db_status = "starting"
DATABASE_READINESS_TIMEOUT_SECONDS = 3.0
CALL_SPEECH_RMS = 0.01
CALL_BARGE_RMS = 0.05
CALL_BARGE_STRONG_RMS = 0.08
CALL_BARGE_ARM_SECONDS = 0.2
CALL_BARGE_HITS = 2
CALL_BARGE_GRACE_SECONDS = 3.0
CALL_SILENCE_SECONDS = 0.8
CALL_MAX_UTTERANCE_SECONDS = 8.0
CALL_TURN_GUARD_SECONDS = 2.5
_recording_origin_tasks: set[asyncio.Task[None]] = set()
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm Sherpa-ONNX before serving requests without blocking the event loop."""
    global db_status, sherpa_status, tts_status
    db_status = "starting"
    try:
        await check_database()
        db_status = "ready"
        logger.info("PostgreSQL is ready")
    except Exception:
        db_status = "error"
        # Keep the API alive so /health/live can distinguish process health from readiness.
        logger.exception("PostgreSQL failed its startup check")

    try:
        await start_ips_runtime()
        logger.info("SODA3 IPS adapter is ready")
    except Exception:
        # Keep the API alive and expose a controlled 503 from /api/ips if the
        # external integration is not configured or cannot be constructed.
        logger.exception("SODA3 IPS adapter failed its startup check")

    sherpa_status = "starting"
    try:
        await asyncio.to_thread(stt_provider.preload)
        sherpa_status = "ready"
        logger.info("Sherpa-ONNX is ready (%s)", stt_label())
    except Exception:
        sherpa_status = "error"
        # Keep the API alive so /health/live can distinguish process health from readiness.
        logger.exception("Sherpa-ONNX failed to load during backend startup")
    tts_status = "starting"
    logger.info("Checking ElevenLabs voice configuration")
    try:
        await asyncio.to_thread(tts_provider.preload)
        tts_status = "ready"
        logger.info("ElevenLabs TTS is ready")
    except Exception:
        tts_status = "error"
        logger.exception("ElevenLabs TTS is not configured")
    try:
        await asyncio.to_thread(rag_embeddings.preload)
        logger.info("E5 embeddings are ready")
    except Exception:
        logger.exception("E5 embeddings failed to load during backend startup")
    from .telephony.runtime import start_telephony, stop_telephony

    await start_telephony()
    try:
        yield
    finally:
        try:
            await stop_telephony()
        except Exception:
            logger.exception("Telnyx shutdown failed")
        try:
            try:
                await stop_ips_runtime()
            except Exception:
                logger.exception("SODA3 IPS cleanup failed")
            try:
                await close_redis()
            except Exception:
                logger.exception("Redis cleanup failed")
        finally:
            await dispose_engine()


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
app.include_router(analytics_router)
app.include_router(commercial_router)
app.include_router(dev_router)
app.include_router(telnyx_router)
app.include_router(whatsapp_router)
app.include_router(agent_state_router)
app.include_router(create_ips_router())


def _health_status() -> dict[str, str]:
    return {
        "status": "ok",
        "sherpa": sherpa_status,
        "tts": tts_status,
        "db": db_status,
        "stt_model": stt_label(),
    }


async def _refresh_database_readiness() -> None:
    """Check the current database connection instead of trusting startup state."""
    global db_status
    try:
        await asyncio.wait_for(check_database(), timeout=DATABASE_READINESS_TIMEOUT_SECONDS)
    except Exception:
        db_status = "error"
        logger.warning("PostgreSQL readiness check failed", exc_info=True)
    else:
        db_status = "ready"


@app.get("/health/live", summary="Comprueba que el proceso de la API está vivo")
def health_live() -> dict[str, str]:
    return _health_status()


@app.get("/health/ready", summary="Comprueba que la API, los modelos y PostgreSQL están listos")
async def health_ready() -> Response:
    await _refresh_database_readiness()
    payload = _health_status()
    if sherpa_status != "ready" or tts_status != "ready" or db_status != "ready":
        return JSONResponse(status_code=503, content=payload)
    payload["status"] = "ready"
    return JSONResponse(content=payload)


@app.get("/health", deprecated=True, summary="Alias de compatibilidad para health/live")
def health() -> dict[str, str]:
    """Keep the legacy path as a liveness check; readiness is /health/ready."""
    return health_live()


@app.get("/api/hello")
def hello() -> dict[str, str]:
    return {"message": "FastAPI + Astro funcionando"}


def _issue_session(user: User) -> LoginResponse:
    try:
        token = create_access_token(user.id)
        expires_in = get_access_token_expire_minutes() * 60
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=500, detail="Authentication is not configured.") from exc
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@app.post("/auth/login", response_model=LoginResponse, summary="Inicia sesión")
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)) -> LoginResponse:
    try:
        user = await session.scalar(select(User).where(User.email == payload.email))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from exc

    if user is None:
        verify_password(payload.password, DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return _issue_session(user)


@app.post("/auth/refresh", response_model=LoginResponse, summary="Renueva el token de acceso")
async def refresh_token(user: User = Depends(get_current_user)) -> LoginResponse:
    return _issue_session(user)


@app.get("/auth/me", response_model=UserResponse, summary="Devuelve el usuario autenticado")
async def auth_me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)


@app.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=201,
    summary="Crea una organización",
)
async def create_organization(
    payload: OrganizationCreate,
    _superadmin: User = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    organization = Organization(name=payload.name, slug=payload.slug)
    session.add(organization)
    try:
        await session.commit()
        await session.refresh(organization)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Organization could not be created.") from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="Organization service unavailable.") from exc
    return OrganizationResponse.model_validate(organization)


@app.post(
    "/organizations/{organization_id}/admins",
    response_model=UserResponse,
    status_code=201,
    summary="Crea un administrador de organización",
)
async def create_organization_admin(
    organization_id: uuid.UUID,
    payload: AdminCreate,
    _superadmin: User = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    try:
        admin = User(
            organization_id=organization.id,
            email=payload.email,
            password_hash=hash_password(payload.password),
            role=UserRole.ADMIN,
        )
    except PasswordValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid password.") from exc
    session.add(admin)
    try:
        await session.commit()
        await session.refresh(admin)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Admin could not be created.") from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="Organization service unavailable.") from exc
    return UserResponse.model_validate(admin)


def _tenant_id(user: User) -> uuid.UUID:
    if user.organization_id is None:
        raise HTTPException(
            status_code=403,
            detail="An organization is required for this operation.",
        )
    return user.organization_id


async def _persist_message(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    channel: str = "voice",
) -> DbMessage:
    message = DbMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        channel=channel,
    )
    session.add(message)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return message


async def _conversation_history(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> tuple[Conversation, list[DbMessage]]:
    conversation = await get_conversation(
        session,
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = await list_messages(
        session,
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    return conversation, messages


@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Crea una conversación persistida",
)
async def create_conversation_endpoint(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    organization_id = _tenant_id(user)
    try:
        conversation = await create_conversation(
            session,
            organization_id=organization_id,
            created_by=user.id,
            channel=payload.channel,
            status=payload.status,
        )
        await session.commit()
        await session.refresh(conversation)
    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Conversation could not be created.") from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="Conversation service unavailable.") from exc
    return ConversationResponse.model_validate(conversation)


@app.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Recupera una conversación y su historial",
)
async def get_conversation_endpoint(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    organization_id = _tenant_id(user)
    conversation, messages = await _conversation_history(
        session,
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    return ConversationResponse(
        id=conversation.id,
        organization_id=conversation.organization_id,
        created_by=conversation.created_by,
        channel=conversation.channel,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )


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
                stt_provider.transcribe_audio,
                audio,
                request.headers.get("content-type", "audio/webm"),
            )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Sherpa-ONNX no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo transcribir el audio.") from exc
    return TranscriptionResponse(text=text)


@app.post("/synthesize", response_class=Response)
async def synthesize(request: SynthesisRequest) -> Response:
    try:
        audio = await asyncio.to_thread(tts_provider.synthesize_wav, request.text)
    except SynthesisNotConfigured as exc:
        raise HTTPException(status_code=503, detail="La síntesis de voz no está configurada.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo sintetizar el texto.") from exc
    return Response(content=audio, media_type="audio/wav")


@app.post("/synthesize/stream", response_class=StreamingResponse)
async def synthesize_stream(request: SynthesisRequest) -> StreamingResponse:
    """Stream ElevenLabs as mono signed-int16 PCM."""
    try:
        await asyncio.to_thread(tts_provider.preload)
        sample_rate = tts_provider.sample_rate()
    except SynthesisNotConfigured as exc:
        raise HTTPException(status_code=503, detail="La síntesis de voz no está configurada.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo iniciar la síntesis de voz.") from exc
    return StreamingResponse(
        tts_provider.stream_audio(request.text),
        media_type=f"audio/L16; rate={sample_rate}; channels=1",
        headers={
            "X-Audio-Sample-Rate": str(sample_rate),
            "X-Audio-Encoding": "signed-int16-le",
        },
    )


class ErrorResponse(BaseModel):
    detail: str = Field(description="Descripción segura del error.")


def _agent_stream(
    prompt: str,
    *,
    messages: list[dict[str, str]] | None,
    organization_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    trace: TraceRecorder | None = None,
):
    kwargs: dict[str, object] = {"messages": messages, "llm": llm_provider}
    if "tool_context" in inspect.signature(stream_agent).parameters:
        kwargs["tool_context"] = ToolContext(
            request_id=f"request-{uuid.uuid4()}",
            organization_id=str(organization_id) if organization_id else None,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=str(user_id) if user_id else None,
        )
    if trace is not None and "trace" in inspect.signature(stream_agent).parameters:
        kwargs["trace"] = trace
    return stream_agent(prompt, **kwargs)


def _event(name: str, payload: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _ask_stream(
    prompt: str,
    messages: list[dict[str, str]] | None = None,
    *,
    session: AsyncSession | None = None,
    conversation_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[str]:
    answer_parts: list[str] = []
    proposal_id = None
    async for name, payload in _agent_stream(
        prompt,
        messages=messages,
        organization_id=organization_id,
        conversation_id=conversation_id,
        user_id=user_id,
    ):
        if name == "token":
            answer_parts.append(str(payload.get("text", "")))
        if name == "done" and session is not None and conversation_id is not None:
            answer = "".join(answer_parts).strip()
            if answer:
                try:
                    await _persist_message(
                        session,
                        conversation_id=conversation_id,
                        role=MessageRole.ASSISTANT,
                        content=answer,
                    )
                except Exception:
                    logger.exception("Assistant message persistence failed")
                    yield _event(
                        "error",
                        {"message": "No se pudo guardar la respuesta de la conversación."},
                    )
                    return
        if name == 'done':
            proposal_id = payload.get('proposal_id')
        yield _event(name, payload)
    # The agent generator must release its conversation lock before transport acknowledgement.
    if proposal_id and organization_id and conversation_id:
        from .agent.store import mark_presented
        await mark_presented(str(organization_id), str(conversation_id), str(proposal_id))


async def _replay_stream(buffered_chunks: list[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    for chunk in buffered_chunks:
        yield chunk
    async for chunk in stream:
        yield chunk


@app.post(
    "/ask",
    response_class=StreamingResponse,
    response_model=None,
    summary="Pregunta a los modelos configurados",
    description=(
        "Devuelve un stream SSE con eventos de retrieval, tools, `token` y `done`. "
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
                        'event: done\ndata: {"provider":"openai","model":"gpt-6-luna"}\n\n'
                    ),
                }
            },
        },
        500: {"model": ErrorResponse, "description": "La configuración del entorno no es válida."},
        502: {"model": ErrorResponse, "description": "Los providers no pudieron responder."},
        503: {"model": ErrorResponse, "description": "No hay API keys configuradas."},
    },
)
async def ask(
    request: AskRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    organization_id = _tenant_id(user)
    try:
        chain = get_model_chain()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Configuración de entorno inválida") from exc
    if not any(config.api_key for config in chain):
        return JSONResponse(
            status_code=503,
            content={"detail": "No hay API keys configuradas para el entorno seleccionado."},
        )

    conversation_id = request.conversation_id
    if conversation_id is not None:
        try:
            _conversation, persisted_messages = await _conversation_history(
                session,
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Conversation service unavailable.") from exc
        history = context_window(
            [
                {"role": message.role.value, "content": message.content}
                for message in persisted_messages
                if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
            ]
        )
        try:
            await _persist_message(
                session,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=request.prompt,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Conversation service unavailable.") from exc
    else:
        if not request.messages:
            raise HTTPException(
                status_code=422,
                detail="conversation_id is required; messages is a temporary compatibility path.",
            )
        history = [message.model_dump() for message in request.messages]

    stream = _ask_stream(
        request.prompt,
        history or None,
        session=session if conversation_id is not None else None,
        conversation_id=conversation_id,
        organization_id=organization_id,
        user_id=user.id,
    )
    buffered_chunks: list[str] = []
    terminal_event = ""
    try:
        while True:
            chunk = await anext(stream)
            buffered_chunks.append(chunk)
            event_name = next(
                (line.removeprefix("event: ").strip() for line in chunk.splitlines() if line.startswith("event:")),
                "",
            )
            if event_name in {"token", "done", "error"}:
                terminal_event = event_name
                break
    except StopAsyncIteration:
        pass

    if not terminal_event:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
        return JSONResponse(status_code=502, content={"detail": "Los providers no respondieron."})

    if terminal_event == "error":
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
        return JSONResponse(status_code=502, content={"detail": "Los providers no pudieron responder."})

    return StreamingResponse(_replay_stream(buffered_chunks, stream), media_type="text/event-stream")


_TRANSCRIPT_LETTER = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")


def _usable_transcript(text: str) -> bool:
    return len(_TRANSCRIPT_LETTER.findall(text)) >= 2


async def _send_call_event(
    websocket: WebSocket,
    event_type: str,
    payload: dict[str, object],
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    """Keep the call's direct JSON transport and fan out a structured copy."""
    await websocket.send_json({"type": event_type, **payload})
    try:
        await realtime_hub.publish(
            RealtimeEvent(
                type=event_type,
                organization_id=organization_id,
                conversation_id=conversation_id,
                payload=payload,
            )
        )
    except Exception:
        # A monitoring subscriber must never break the call's direct transport.
        logger.exception("Realtime event publish failed: %s", event_type)


async def _forward_call_audio(websocket: WebSocket, speech: Any) -> None:
    announced = False
    async for audio_chunk in speech.audio():
        if not audio_chunk:
            continue
        if not announced:
            announced = True
            await websocket.send_json({"type": "tts.format", "sample_rate": int(speech.sample_rate)})
        await websocket.send_bytes(audio_chunk)


async def _close_call_speech(speech: Any, forward: asyncio.Task[None] | None, *, cancel: bool) -> None:
    try:
        if cancel:
            await speech.cancel()
        else:
            await speech.finish()
    finally:
        if forward is not None:
            if cancel:
                forward.cancel()
            await asyncio.gather(forward, return_exceptions=True)


async def _persist_recording_origin(call_id: uuid.UUID) -> None:
    """Remember that a browser demo recording starts at offset 0."""
    try:
        from sqlalchemy import update

        from .db.session import get_session_factory

        async with get_session_factory()() as db:
            await db.execute(
                update(Call).where(Call.id == call_id).values(recording_offset_ms=0)
            )
            await db.commit()
    except Exception:
        logger.exception("Could not store recording offset")


def _announce_agent_phrase(heard: CallSession, text: str) -> None:
    cleaned = text.strip()
    if not cleaned or heard.closed:
        return
    at = agent_heard_ms(heard)
    timeline.record(heard, "transcript.final", {"speaker": "agent", "text": cleaned}, at_offset_ms=at)
    heard.agent_state = "speaking"
    timeline.record(heard, "agent.state", {"state": "speaking"}, at_offset_ms=at)


def _mark_listening(heard: CallSession | None) -> None:
    if heard is None or heard.closed:
        return
    heard.agent_state = "listening"
    timeline.record(heard, "agent.state", {"state": "listening"})


def _customer_mark(heard: CallSession | None) -> int | None:
    if heard is None:
        return None
    if heard.utterance_offset_ms is not None:
        mark = heard.utterance_offset_ms
        heard.utterance_offset_ms = None
        return mark
    return timeline_ms(heard.recording_offset_ms, len(heard.customer_pcm) // 2)


def _record_customer_transcript(heard: CallSession | None, text: str, at_offset_ms: int | None) -> None:
    cleaned = text.strip()
    if heard is None or heard.closed or not cleaned:
        return
    timeline.record(
        heard,
        "transcript.final",
        {"speaker": "customer", "text": cleaned},
        at_offset_ms=at_offset_ms,
    )


async def _speak_chunk(
    websocket: WebSocket,
    text: str,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    heard: CallSession | None = None,
) -> None:
    if heard is not None and text.strip() and not heard.closed:
        _announce_agent_phrase(heard, text)
    if tts_status != "ready" or not text.strip():
        return
    await _send_call_event(
        websocket,
        "tts.started",
        {"text": text},
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    speech = open_speech_turn(tts_provider)
    forward: asyncio.Task[None] | None = None
    try:
        await speech.open()
        forward = asyncio.create_task(_forward_call_audio(websocket, speech))
        await speech.say(text)
        await _close_call_speech(speech, forward, cancel=False)
    except asyncio.CancelledError:
        await _close_call_speech(speech, forward, cancel=True)
        raise
    except Exception:
        logger.exception("Call TTS failed")
        await _close_call_speech(speech, forward, cancel=True)
        await _send_call_event(
            websocket,
            "error",
            {"message": "No se pudo sintetizar la voz."},
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        return
    await _send_call_event(
        websocket,
        "tts.completed",
        {},
        organization_id=organization_id,
        conversation_id=conversation_id,
    )


def _call_demo_greeting() -> str:
    from .agent.phrases import IPS_GREETING
    return IPS_GREETING


async def _run_call_turn(
    websocket: WebSocket,
    prompt: str,
    history: list[dict[str, str]],
    *,
    session: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    call_id: uuid.UUID | None = None,
    heard: CallSession | None = None,
    customer_offset_ms: int | None = None,
) -> bool:
    try:
        await _persist_message(
            session,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=prompt,
        )
    except Exception:
        logger.exception("Customer message persistence failed")
        await _send_call_event(
            websocket,
            "error",
            {"message": "No se pudo guardar la conversación."},
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        return False
    recorder = TraceRecorder(
        call_id=str(call_id) if call_id else None,
        conversation_id=str(conversation_id),
        organization_id=str(organization_id),
    )
    # Start the root span before policy/runtime branching.  Deterministic
    # turns can complete without entering the model generator, but they are
    # still valuable replay telemetry and must be persisted.
    recorder.start(prompt, history, [])
    await _send_call_event(
        websocket,
        "turn.started",
        {},
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    answer = ""
    buffer = ""
    failed = False
    proposal_id = None
    last_done: dict[str, Any] = {}
    reservation_confirmed = False
    trace_saved = False
    speech = None
    forward: asyncio.Task[None] | None = None
    said = False
    speech_closed = False

    async def speak_sentence(text: str) -> None:
        nonlocal said
        if speech is None or not text.strip():
            return
        said = True
        if heard is not None:
            _announce_agent_phrase(heard, text)
        await _send_call_event(
            websocket,
            "tts.started",
            {"text": text},
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        await speech.say(text)

    async def end_speech(*, cancel: bool) -> None:
        nonlocal speech_closed
        if speech is None or speech_closed:
            return
        speech_closed = True
        await _close_call_speech(speech, forward, cancel=cancel)

    async def finalize_trace() -> None:
        nonlocal trace_saved
        if trace_saved:
            return
        recorder.finish(
            answer=answer,
            provider=last_done.get('provider'),
            model=last_done.get('model'),
            status='error' if failed else 'ok',
        )
        await save_trace(recorder)
        trace_saved = True

    try:
        if tts_status == "ready":
            speech = open_speech_turn(tts_provider)
            try:
                await speech.open()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ElevenLabs connection failed")
                await speech.cancel()
                speech = None
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "No se pudo iniciar la voz."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
            else:
                forward = asyncio.create_task(_forward_call_audio(websocket, speech))
        async for name, payload in _agent_stream(
            prompt,
            messages=history or None,
            organization_id=organization_id,
            conversation_id=conversation_id,
            user_id=user_id,
            trace=recorder,
        ):
            if name == "error":
                failed = True
                if heard is not None:
                    timeline.record(heard, "agent.error", {"message": payload.get("message") or "error"})
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": payload["message"]},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                break
            if name == 'done':
                last_done = payload
                proposal_id = payload.get('proposal_id')
            if name in {"tool.started", "tool.completed", "rag.started", "rag.completed", "agent.signals"}:
                if (name == "tool.completed" and payload.get("tool") == "create_booking"
                        and payload.get("ok") is True):
                    reservation_confirmed = True
                if heard is not None:
                    if name == "agent.signals":
                        timeline.record(heard, name, payload, at_offset_ms=customer_offset_ms)
                    else:
                        timeline.record(heard, name, payload)
                await _send_call_event(
                    websocket,
                    name,
                    payload,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            if name != "token":
                continue
            token = payload["text"]
            answer += token
            buffer += token
            await _send_call_event(
                websocket,
                "agent.token",
                {"text": token},
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
            chunk, buffer = _take_semantic_chunk(buffer)
            if chunk:
                await speak_sentence(chunk)
        if buffer.strip():
            await speak_sentence(buffer.strip())
        await end_speech(cancel=not said)
        _mark_listening(heard)
        if said:
            await _send_call_event(
                websocket,
                "tts.completed",
                {},
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
        if proposal_id and not failed:
            from .agent.store import mark_presented
            await mark_presented(str(organization_id), str(conversation_id), str(proposal_id))
        if not failed and answer.strip():
            try:
                await _persist_message(
                    session,
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                )
            except Exception:
                logger.exception("Assistant message persistence failed")
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "No se pudo guardar la conversación."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                return False
            history.extend(
                [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
            )
            history[:] = context_window(history)
        # Persist before announcing completion so a PCM client cannot submit
        # another turn while the trace write is still keeping this task alive.
        await finalize_trace()
        await _send_call_event(
            websocket,
            "turn.completed",
            {"text": answer, "end_call": reservation_confirmed and not failed},
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        return reservation_confirmed and not failed
    except asyncio.CancelledError:
        failed = True
        await end_speech(cancel=True)
        try:
            await _send_call_event(
                websocket,
                "turn.cancelled",
                {},
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
        except Exception:
            pass
        raise
    except Exception:
        failed = True
        await end_speech(cancel=True)
        logger.exception("Call turn failed")
        try:
            await _send_call_event(
                websocket,
                "error",
                {"message": "No se pudo completar la llamada."},
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
        except Exception:
            pass
        return False
    finally:
        await finalize_trace()


async def _receive_json_message(websocket: WebSocket) -> dict[str, object] | None:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect
    text = message.get("text")
    if not isinstance(text, str) or not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _finish_call(
    session: AsyncSession,
    call: Call,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    status: CallStatus = CallStatus.ENDED,
) -> None:
    if call.status != CallStatus.ACTIVE:
        return
    call.status = status
    call.ended_at = datetime.now(UTC)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Call status persistence failed")
        return
    if status == CallStatus.ENDED and not await enqueue_enrichment(organization_id, conversation_id):
        logger.warning(
            "Enrichment job was not queued after call completion organization_id=%s conversation_id=%s",
            organization_id,
            conversation_id,
        )


@app.websocket("/ws/events")
async def events_socket(
    websocket: WebSocket,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Subscribe to tenant-scoped JSON events after authenticating."""
    await websocket.accept()
    organization_id: uuid.UUID | None = None
    try:
        auth_payload = await _receive_json_message(websocket)
        token = auth_payload.get("token") if auth_payload is not None else None
        if auth_payload is None or auth_payload.get("type") != "auth" or not isinstance(token, str):
            await websocket.close(code=4401)
            return
        try:
            user = await authenticate_token(token, session)
        except InvalidTokenError:
            await websocket.close(code=4401)
            return
        except Exception:
            logger.exception("Realtime events authentication failed")
            await websocket.close(code=1011)
            return

        organization_id = user.organization_id
        if organization_id is None:
            await websocket.close(code=4403)
            return
        realtime_hub.connect(websocket, organization_id)
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await websocket.close(code=1003)
                return
            # The endpoint is a subscriber; text messages are intentionally ignored.
    except WebSocketDisconnect:
        logger.info("Realtime events WebSocket disconnected")
    except Exception:
        logger.exception("Realtime events WebSocket failed")
    finally:
        if organization_id is not None:
            realtime_hub.disconnect(websocket, organization_id)


@app.websocket("/ws/call")
async def call_socket(
    websocket: WebSocket,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Live PCM: authenticate and attach a tenant conversation before media."""
    await websocket.accept()
    call_record: Call | None = None
    heard: CallSession | None = None
    mute_from: list[int | None] = [None]
    call_completion_status = CallStatus.ENDED
    try:
        auth_payload = await _receive_json_message(websocket)
        token = auth_payload.get("token") if auth_payload is not None else None
        if auth_payload is None or auth_payload.get("type") != "auth" or not isinstance(token, str):
            await websocket.close(code=4401)
            return
        try:
            user = await authenticate_token(token, session)
        except InvalidTokenError:
            await websocket.close(code=4401)
            return
        except Exception:
            logger.exception("Call authentication failed")
            await websocket.close(code=1011)
            return

        organization_id = user.organization_id
        if organization_id is None:
            await websocket.close(code=4403)
            return
        attach_payload = await _receive_json_message(websocket)
        conversation_value = (
            attach_payload.get("conversation_id") if attach_payload is not None else None
        )
        if (
            attach_payload is None
            or attach_payload.get("type") != "conversation.attach"
            or not isinstance(conversation_value, str)
        ):
            await websocket.close(code=4400)
            return
        try:
            conversation_id = uuid.UUID(conversation_value)
        except ValueError:
            await websocket.close(code=4400)
            return
        try:
            conversation = await get_conversation(
                session,
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                await websocket.close(code=4403)
                return
            persisted_messages = await list_messages(
                session,
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
            call_record = Call(
                organization_id=organization_id,
                conversation_id=conversation_id,
                status=CallStatus.ACTIVE,
                recording_offset_ms=0,
            )
            session.add(call_record)
            await session.commit()
            heard = CallSession(
                call_id=call_record.id,
                token="",
                telnyx_call_control_id="",
                organization_id=organization_id,
                system_user_id=user.id,
                conversation_id=conversation_id,
            )
            heard.recording_offset_ms = 0
            timeline.record(heard, "lifecycle", {"state": "ACTIVE"})
            origin = asyncio.create_task(_persist_recording_origin(call_record.id))
            _recording_origin_tasks.add(origin)
            origin.add_done_callback(_recording_origin_tasks.discard)
        except Exception:
            logger.exception("Call conversation lookup failed")
            await websocket.close(code=1011)
            return
        history: list[dict[str, str]] = context_window(
            [
                {"role": message.role.value, "content": message.content}
                for message in persisted_messages
                if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
            ]
        )
        await websocket.send_json(
            {
                "type": "call.connected",
                "tts": tts_status,
                "stt_model": stt_label(),
                "conversation_id": str(conversation.id),
            }
        )
        if tts_status == "ready":
            ready_rate = await asyncio.to_thread(tts_provider.sample_rate)
            await websocket.send_json({"type": "tts.format", "sample_rate": ready_rate})
        audio_mime = "audio/webm"
        pcm_mode = True
        sample_rate = 16000
        stream = None
        last_partial = ""
        last_voice_at = 0.0
        first_voice_at = 0.0
        ignore_until = 0.0
        barge_armed_at = 0.0
        barge_hits = 0
        barge_pending = False  # playback held while we wait to see if the barge was real noise
        barge_last_voice_at = 0.0
        barge_resume_task: asyncio.Task[None] | None = None
        turn_task: asyncio.Task[bool] | None = None
        ending_call = False
        barge_pcm = bytearray()  # bounded pre-roll so a barge does not lose the first words
        overlap_voice_at = 0.0
        stream = await asyncio.to_thread(stt_provider.create_stream)
        utterance_pcm = bytearray()
        speech_sanitizer = SpeechSanitizer(CALL_SPEECH_RMS)
        idle_since = time.monotonic()
        last_silence_prompt = None

        async def _silence_prompt(text: str) -> bool:
            await _send_call_event(websocket, 'agent.token', {'text': text},
                organization_id=organization_id, conversation_id=conversation_id)
            history.append({'role': 'assistant', 'content': text})
            session.add(DbMessage(conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                content=text, channel='voice'))
            await session.commit()
            await _speak_chunk(websocket, text, organization_id=organization_id,
                conversation_id=conversation_id, heard=heard)
            _mark_listening(heard)
            await _send_call_event(websocket, 'turn.completed', {},
                organization_id=organization_id, conversation_id=conversation_id)
            return False

        async def _speak_greeting(text: str) -> bool:
            await _speak_chunk(websocket, text, organization_id=organization_id,
                conversation_id=conversation_id, heard=heard)
            _mark_listening(heard)
            await _send_call_event(websocket, 'turn.completed', {},
                organization_id=organization_id, conversation_id=conversation_id)
            return False

        if not history:
            welcome = _call_demo_greeting()
            await _send_call_event(websocket, 'turn.started', {},
                organization_id=organization_id, conversation_id=conversation_id)
            await _send_call_event(websocket, 'agent.token', {'text': welcome},
                organization_id=organization_id, conversation_id=conversation_id)
            history.append({'role': 'assistant', 'content': welcome})
            await _persist_message(session, conversation_id=conversation_id,
                role=MessageRole.ASSISTANT, content=welcome)
            now = time.monotonic()
            barge_armed_at = now + CALL_BARGE_ARM_SECONDS
            turn_task = asyncio.create_task(_speak_greeting(welcome))

        async def _resume_playback() -> None:
            """Give the held playback back to the client after a false-positive barge."""
            nonlocal barge_pending, barge_resume_task
            barge_pending = False
            pending_task = barge_resume_task
            if pending_task is not None and pending_task is not asyncio.current_task():
                pending_task.cancel()
            barge_resume_task = None
            try:
                await websocket.send_json({"type": "tts.resume"})
            except Exception:
                pass

        async def _resume_after_barge_silence() -> None:
            """Resume the agent only after the grace window passes without any customer voice."""
            while barge_pending:
                await asyncio.sleep(CALL_BARGE_GRACE_SECONDS)
                if not barge_pending:
                    return
                if time.monotonic() - barge_last_voice_at >= CALL_BARGE_GRACE_SECONDS:
                    break
            if barge_pending:
                await _resume_playback()

        async def _soft_barge() -> None:
            """Hold agent playback instead of cancelling: the barge may just be noise."""
            nonlocal barge_pending, barge_resume_task, barge_hits, barge_armed_at, barge_last_voice_at, ignore_until, last_partial, overlap_voice_at
            if barge_pending or turn_task is None or turn_task.done():
                return
            buffered = bytes(barge_pcm)
            barge_pcm.clear()
            overlap_voice_at = 0.0
            barge_hits = 0
            barge_armed_at = 0.0
            last_partial = ""
            barge_pending = True
            barge_last_voice_at = time.monotonic()
            if heard is not None:
                mute_from[0] = len(heard.agent_pcm)
            if stream is not None:
                await asyncio.to_thread(stt_provider.reset_stream, stream)
                if buffered:
                    try:
                        clean = speech_sanitizer.sanitize(buffered, sample_rate)
                        await asyncio.to_thread(stt_provider.feed_pcm, stream, clean, sample_rate)
                    except Exception:
                        logger.exception("Barge pre-roll transcription failed")
            ignore_until = 0.0
            try:
                await websocket.send_json({"type": "tts.pause"})
            except Exception:
                pass
            barge_resume_task = asyncio.create_task(_resume_after_barge_silence())

        async def _confirm_barge() -> None:
            """The customer really spoke: drop the interrupted turn and its queued audio."""
            nonlocal turn_task, barge_pending, barge_resume_task, barge_hits, barge_armed_at, last_partial, last_voice_at, first_voice_at, ignore_until
            barge_pending = False
            if barge_resume_task is not None:
                barge_resume_task.cancel()
                barge_resume_task = None
            barge_hits = 0
            task = turn_task
            turn_task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await websocket.send_json({"type": "tts.cancel"})
                except Exception:
                    pass
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Call turn cancel failed")
            if heard is not None and mute_from[0] is not None:
                mute_agent_from(heard, mute_from[0])
                mute_from[0] = None
            last_partial = ""
            last_voice_at = time.monotonic()
            first_voice_at = last_voice_at
            ignore_until = 0.0
            barge_armed_at = 0.0

        async def _reap_turn() -> None:
            nonlocal ending_call, turn_task, last_partial, last_voice_at, first_voice_at
            nonlocal ignore_until, barge_armed_at, barge_hits, stream, overlap_voice_at
            if turn_task is None or not turn_task.done():
                return
            task = turn_task
            turn_task = None
            barge_hits = 0
            barge_armed_at = 0.0
            now = time.monotonic()
            tail = b"" if barge_pending else recent_speech_tail(
                barge_pcm,
                last_speech_at=overlap_voice_at,
                now=now,
                sample_rate=sample_rate,
            )
            barge_pcm.clear()
            overlap_voice_at = 0.0
            last_partial = ""
            last_voice_at = 0.0
            first_voice_at = 0.0
            ignore_until = 0.0
            if barge_pending:
                # The reply finished while its playback was held: let the client drain it.
                await _resume_playback()
            try:
                if await task:
                    ending_call = True
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Call turn failed")
            if stream is not None:
                await asyncio.to_thread(stt_provider.reset_stream, stream)
                utterance_pcm.clear()
                if tail and not ending_call:
                    clean_tail = speech_sanitizer.sanitize(tail, sample_rate)
                    try:
                        await asyncio.to_thread(stt_provider.feed_pcm, stream, clean_tail, sample_rate)
                    except Exception:
                        logger.exception("Overlap tail transcription failed")
                    else:
                        utterance_pcm.extend(clean_tail)
                        last_voice_at = now
                        first_voice_at = now - (len(tail) / 2) / sample_rate

        async def _start_turn(prompt: str) -> None:
            nonlocal turn_task, last_partial, last_voice_at, first_voice_at, stream, ignore_until, barge_armed_at, barge_hits, overlap_voice_at
            if ending_call:
                return
            if barge_pending:
                await _confirm_barge()
            flushed = ""
            if stream is not None:
                try:
                    flushed = await asyncio.to_thread(stt_provider.finish_stream, stream, sample_rate)
                except Exception:
                    logger.exception("Sherpa flush failed")
                stream = await asyncio.to_thread(stt_provider.create_stream)
            final = flushed.strip() if _usable_transcript(flushed) else prompt.strip()
            if not _usable_transcript(final) and utterance_pcm:
                rescued = await asyncio.to_thread(recover_short_transcript, bytes(utterance_pcm), sample_rate)
                if _usable_transcript(rescued):
                    final = rescued.strip()
            utterance_pcm.clear()
            last_partial = ""
            barge_pcm.clear()
            overlap_voice_at = 0.0
            last_voice_at = 0.0
            first_voice_at = 0.0
            if not _usable_transcript(final):
                ignore_until = 0.0
                return
            now = time.monotonic()
            ignore_until = now + CALL_TURN_GUARD_SECONDS
            barge_armed_at = now + CALL_BARGE_ARM_SECONDS
            barge_hits = 0
            started_at = heard.utterance_offset_ms if heard is not None else None
            if heard is not None:
                heard.utterance_offset_ms = None
            _record_customer_transcript(heard, final, started_at)
            await _send_call_event(
                websocket,
                "customer.transcript",
                {"text": final},
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
            turn_task = asyncio.create_task(
                _run_call_turn(
                    websocket,
                    final,
                    history,
                    session=session,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    user_id=user.id,
                    call_id=call_record.id if call_record else None,
                    heard=heard,
                    customer_offset_ms=started_at,
                )
            )

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                if barge_resume_task is not None:
                    barge_resume_task.cancel()
                    barge_resume_task = None
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()
                    try:
                        await turn_task
                    except (asyncio.CancelledError, Exception):
                        pass
                break
            await _reap_turn()
            if ending_call:
                continue
            raw = message.get("bytes")
            text = message.get("text")
            if raw is not None:
                if pcm_mode:
                    level, voiced, rms = pcm_speech_features(raw, sample_rate)
                    await websocket.send_json({"type": "wave.level", "value": level, "source": "customer"})
                    now = time.monotonic()
                    busy = turn_task is not None and not turn_task.done()
                    speaking = voiced or rms >= CALL_SPEECH_RMS
                    if heard is not None:
                        if speaking and heard.utterance_offset_ms is None:
                            heard.utterance_offset_ms = timeline_ms(
                                heard.recording_offset_ms, len(heard.customer_pcm) // 2
                            )
                        place_customer_pcm(heard, raw, sample_rate)
                    if busy or speaking:
                        idle_since = now
                    elif first_voice_at <= 0 and now - idle_since > 10:
                        from .agent.phrases import SILENCE, pick
                        last_silence_prompt = pick(SILENCE, last_silence_prompt)
                        idle_since = now
                        barge_armed_at = now + CALL_BARGE_ARM_SECONDS
                        turn_task = asyncio.create_task(_silence_prompt(last_silence_prompt))
                        continue
                    if speaking and (not busy or barge_pending) and now >= ignore_until:
                        last_voice_at = now
                        # Only pitch-like voice extends the grace window; sustained
                        # broadband noise must still time out and resume playback.
                        if barge_pending and voiced:
                            barge_last_voice_at = now
                        if first_voice_at <= 0:
                            first_voice_at = now
                    if busy and not barge_pending:
                        if speaking:
                            overlap_voice_at = now
                        if now >= barge_armed_at and rms >= CALL_BARGE_RMS:
                            barge_hits += 1
                        else:
                            barge_hits = 0
                        strong = rms >= CALL_BARGE_STRONG_RMS
                        enough = barge_hits >= CALL_BARGE_HITS or (
                            voiced and barge_hits >= max(2, CALL_BARGE_HITS // 2)
                        )
                        if now >= barge_armed_at and (strong or enough):
                            barge_hits = 0
                            await _soft_barge()
                        else:
                            barge_pcm.extend(raw)
                            del barge_pcm[:-64000]  # keep at most two seconds of pre-roll
                            continue
                    if stream is None or now < ignore_until:
                        continue
                    clean = speech_sanitizer.sanitize(raw, sample_rate)
                    try:
                        partial, ended = await asyncio.to_thread(stt_provider.feed_pcm, stream, clean, sample_rate)
                    except Exception:
                        logger.exception("Call transcription failed")
                        await _send_call_event(
                            websocket,
                            "error",
                            {"message": "No se pudo transcribir el audio."},
                            organization_id=organization_id,
                            conversation_id=conversation_id,
                        )
                        continue
                    utterance_pcm.extend(clean)
                    if partial and partial != last_partial:
                        last_partial = partial
                        if _usable_transcript(partial):
                            if barge_pending:
                                barge_last_voice_at = time.monotonic()
                            await _send_call_event(
                                websocket,
                                "customer.partial",
                                {"text": partial},
                                organization_id=organization_id,
                                conversation_id=conversation_id,
                            )
                    prompt = last_partial.strip()
                    silent = last_voice_at > 0 and now - last_voice_at >= CALL_SILENCE_SECONDS
                    too_long = (
                        first_voice_at > 0
                        and now - first_voice_at >= CALL_MAX_UTTERANCE_SECONDS
                    )
                    if silent or ended or too_long:
                        await _start_turn(prompt)
                    continue
                if ending_call:
                    continue
                try:
                    async with transcription_lock:
                        prompt = await asyncio.to_thread(stt_provider.transcribe_audio, raw, audio_mime)
                except Exception:
                    logger.exception("Call transcription failed")
                    await _send_call_event(
                        websocket,
                        "error",
                        {"message": "No se pudo transcribir el audio."},
                        organization_id=organization_id,
                        conversation_id=conversation_id,
                    )
                    continue
                if not prompt.strip():
                    await websocket.send_json({"type": "transcript.empty"})
                    continue
                spoken_at = _customer_mark(heard)
                _record_customer_transcript(heard, prompt, spoken_at)
                await _send_call_event(
                    websocket,
                    "customer.transcript",
                    {"text": prompt},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                ending_call = await _run_call_turn(
                    websocket,
                    prompt,
                    history,
                    session=session,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    user_id=user.id,
                    heard=heard,
                    customer_offset_ms=spoken_at,
                )
                continue
            if ending_call:
                continue
            if not text:
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "Mensaje de llamada inválido."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "Mensaje de llamada inválido."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            if not isinstance(payload, dict):
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "Mensaje de llamada inválido."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            if payload.get("type") == "pcm.start":
                idle_since = time.monotonic()
                rate = payload.get("sample_rate")
                if isinstance(rate, int) and rate > 0:
                    sample_rate = rate
                pcm_mode = True
                last_partial = ""
                last_voice_at = 0.0
                first_voice_at = 0.0
                barge_hits = 0
                barge_armed_at = 0.0
                overlap_voice_at = 0.0
                barge_pcm.clear()
                utterance_pcm.clear()
                if barge_pending:
                    # Never leave the client's queue suspended: release it explicitly.
                    await _resume_playback()
                if stream is not None:
                    await asyncio.to_thread(stt_provider.reset_stream, stream)
                else:
                    stream = await asyncio.to_thread(stt_provider.create_stream)
                continue
            if payload.get("type") == "pcm.stop":
                pcm_mode = False
                last_partial = ""
                last_voice_at = 0.0
                first_voice_at = 0.0
                barge_hits = 0
                barge_armed_at = 0.0
                overlap_voice_at = 0.0
                barge_pcm.clear()
                utterance_pcm.clear()
                if barge_pending:
                    # Never leave the client's queue suspended: release it explicitly.
                    await _resume_playback()
                if stream is not None:
                    await asyncio.to_thread(stt_provider.reset_stream, stream)
                continue
            if payload.get("type") == "barge":
                busy = turn_task is not None and not turn_task.done()
                if busy:
                    await _soft_barge()
                else:
                    last_partial = ""
                    last_voice_at = time.monotonic()
                    first_voice_at = last_voice_at
                    ignore_until = 0.0
                    barge_hits = 0
                    barge_armed_at = 0.0
                    # The speak task can finish before the client drains the queue
                    # (the greeting). Drop that audio instead of resuming it.
                    try:
                        await websocket.send_json({"type": "tts.cancel"})
                    except Exception:
                        pass
                continue
            if payload.get("type") == "audio":
                mime = payload.get("mime")
                if isinstance(mime, str) and mime.strip():
                    audio_mime = mime
                continue
            if payload.get("type") != "turn":
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "Mensaje de llamada inválido."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": "El mensaje está vacío."},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                continue
            typed_at = _customer_mark(heard)
            _record_customer_transcript(heard, prompt, typed_at)
            ending_call = await _run_call_turn(
                websocket,
                prompt,
                history,
                session=session,
                organization_id=organization_id,
                conversation_id=conversation_id,
                user_id=user.id,
                call_id=call_record.id if call_record else None,
                heard=heard,
                customer_offset_ms=typed_at,
            )
    except WebSocketDisconnect:
        logger.info("Call WebSocket disconnected")
    except Exception:
        call_completion_status = CallStatus.FAILED
        logger.exception("Call WebSocket failed")
    finally:
        if heard is not None:
            heard.closed = True
            try:
                await telephony_runtime._store_heard_recording(heard)
                timeline.record(heard, "lifecycle", {"state": "ENDED"})
            except Exception:
                logger.exception("Demo recording persistence failed")
        if call_record is not None:
            await _finish_call(
                session,
                call_record,
                organization_id=call_record.organization_id,
                conversation_id=call_record.conversation_id,
                status=call_completion_status,
            )
