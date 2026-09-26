import asyncio
import inspect
import json
import logging
import os
import queue
import re
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

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
from .features.agent.service import stream_agent
from .agent.tools.contracts import ToolContext
from .features.transcription.service import pcm_speech_features, stt_label
from .features.chat.schemas import (
    AskRequest,
    ConversationCreate,
    ConversationResponse,
)
from .features.transcription.schemas import TranscriptionResponse
from .features.synthesis.schemas import SynthesisRequest
from .providers import (
    ProviderError,
    llm_provider as default_llm_provider,
    stt_provider as default_stt_provider,
    tts_provider as default_tts_provider,
)
from .providers.contracts import LLMProvider, SpeechToTextProvider, TextToSpeechProvider
from .realtime.events import RealtimeEvent
from .realtime.hub import RealtimeHub
from .platform.rag.runtime import store as rag_store, embeddings as rag_embeddings, retriever as rag_retriever
from .platform.rag.ingestion import RagIngestionService
from .platform.rag.retrieval import ProgressiveRetriever
from .platform.rag.extraction import RagExtractionError
from .analytics.router import router as analytics_router
from .platform.queue import enqueue_enrichment
from .commercial.router import router as commercial_router
from .telephony.router import router as telnyx_router


logger = logging.getLogger("hackathon.voice")

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
rag_ingestion = RagIngestionService(rag_store, rag_embeddings, rag_retriever)
sherpa_status = "starting"
tts_status = "starting"
db_status = "starting"
DATABASE_READINESS_TIMEOUT_SECONDS = 3.0
CALL_SPEECH_RMS = 0.02
CALL_BARGE_RMS = 0.05
CALL_BARGE_STRONG_RMS = 0.08
CALL_SILENCE_SECONDS = 0.8
CALL_MAX_UTTERANCE_SECONDS = 8.0
CALL_TURN_GUARD_SECONDS = 2.5
CALL_POST_TURN_GUARD_SECONDS = 0.45
DEMO_KNOWLEDGE_CANDIDATES = (
    Path(__file__).resolve().parent / "platform" / "rag" / "demo_corpus.md",
    REPOSITORY_ROOT / "tests" / "fixtures" / "rag" / "corpus_v1.md",
)


def demo_knowledge_path() -> Path | None:
    for path in DEMO_KNOWLEDGE_CANDIDATES:
        if path.is_file():
            return path
    return None


async def seed_demo_knowledge() -> None:
    path = demo_knowledge_path()
    if path is None:
        return
    try:
        active = await rag_store.get_active_document()
        if active:
            hits = await rag_retriever.ensure_document_chunks(active)
            if hits:
                return
        result = await rag_ingestion.replace(path.read_bytes(), path.name)
        logger.info(
            "Seeded demo knowledge %s (%s chunks)",
            result.get("filename"),
            result.get("chunks"),
        )
    except Exception:
        logger.exception("Demo knowledge could not be seeded")


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
    logger.info("Loading Piper TTS before accepting requests")
    try:
        await asyncio.to_thread(tts_provider.preload)
        tts_status = "ready"
        logger.info("Piper TTS is ready")
    except Exception:
        tts_status = "error"
        logger.exception("Piper TTS failed to load during backend startup")
    try:
        await asyncio.to_thread(rag_embeddings.preload)
        logger.info("E5 embeddings are ready")
    except Exception:
        logger.exception("E5 embeddings failed to load during backend startup")
    await seed_demo_knowledge()
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
app.include_router(telnyx_router)


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


@app.put("/api/rag/document")
async def replace_rag_document(request: Request) -> dict:
    filename = request.headers.get("x-filename", "knowledge.txt")
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = next((value for value in form.values() if hasattr(value, "read")), None)
        if upload is None:
            data = b""
        else:
            filename = getattr(upload, "filename", None) or filename
            data = await upload.read()
    else:
        data = await request.body()
    max_mb = int(os.getenv("RAG_MAX_UPLOAD_MB", "10"))
    if not data:
        raise HTTPException(status_code=422, detail="RAG_EMPTY_UPLOAD")
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="RAG_UPLOAD_TOO_LARGE")
    try:
        return await rag_ingestion.replace(data, filename)
    except RagExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("RAG ingestion failed")
        raise HTTPException(status_code=503, detail="RAG_UNAVAILABLE") from exc


@app.get("/api/rag/status")
async def rag_status() -> dict:
    try:
        active = await rag_store.get_active_document()
    except Exception:
        return {"available": False, "document": None, "embedding_model": rag_embeddings.model_name, "dimensions": rag_embeddings.dimensions}
    if active:
        await rag_retriever.ensure_document_chunks(active)
    hits = rag_retriever._hits.get(active or "", [])
    return {"available": True, "document": ({"document_id": active, "filename": hits[0].metadata.get("source_filename"), "chunks": len(hits)} if active and hits else None), "embedding_model": rag_embeddings.model_name, "dimensions": rag_embeddings.dimensions}


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    debug: bool = False


@app.post("/internal/rag/search")
async def internal_rag_search(payload: RagSearchRequest) -> dict:
    try:
        result = await rag_retriever.search(payload.query, debug=payload.debug)
    except Exception:
        logger.exception("RAG search failed")
        return {"evidence_state": "INSUFFICIENT", "level_reached": 0, "rewrite_used": False, "hits": [], "knowledge_status": "unavailable"}
    hits = [{"chunk_id": h.chunk_id, "content": h.content, "metadata": h.metadata, "score": h.score} for h in result.hits]
    return {"evidence_state": result.evidence_state, "level_reached": result.level_reached, "rewrite_used": result.rewrite_used, "hits": hits, "source_map": result.source_map, "debug": result.debug if payload.debug else {}}


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
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Piper TTS no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo sintetizar el texto.") from exc
    return Response(content=audio, media_type="audio/wav")


@app.post("/synthesize/stream", response_class=StreamingResponse)
async def synthesize_stream(request: SynthesisRequest) -> StreamingResponse:
    """Stream Piper TTS as mono signed-int16 PCM, using one resident model worker."""
    try:
        sample_rate = await asyncio.to_thread(tts_provider.sample_rate)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Piper TTS no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo cargar Piper TTS.") from exc
    return StreamingResponse(
        tts_provider.stream_audio(request.text),
        media_type=f"audio/L16; rate={sample_rate}; channels=1",
        headers={
            "X-Audio-Sample-Rate": str(sample_rate),
            "X-Audio-Encoding": "signed-int16-le",
        },
    )


def _take_semantic_chunk(buffer: str, flush: bool = False) -> tuple[str, str]:
    if flush:
        return buffer.strip(), ""
    sentence = re.search(r"[.!?](?:[\"'»”)]*)?(?=\s|$)", buffer)
    if sentence and len(buffer[: sentence.end()].split()) >= 2:
        return buffer[: sentence.end()].strip(), buffer[sentence.end() :].lstrip()
    return "", buffer


async def _stream_tts_chunk(text: str):
    """Bridge the blocking Piper TTS generator without buffering its audio."""
    audio_queue: queue.Queue[bytes | BaseException | None] = queue.Queue()

    def generate() -> None:
        try:
            for chunk in tts_provider.stream_audio(text):
                audio_queue.put(chunk)
        except BaseException as exc:
            audio_queue.put(exc)
        finally:
            audio_queue.put(None)

    worker = asyncio.create_task(asyncio.to_thread(generate))
    try:
        while True:
            chunk = await asyncio.to_thread(audio_queue.get)
            if chunk is None:
                break
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def _voice_audio_stream(
    prompt: str,
    history: list[dict[str, str]] | None,
    timings: dict[str, float],
):
    chunks: asyncio.Queue[str | BaseException | None] = asyncio.Queue()

    async def produce_text_chunks() -> None:
        buffer = ""
        try:
            async for name, payload in stream_agent(
                prompt,
                messages=history,
                llm=llm_provider,
            ):
                if name == "error":
                    raise ProviderError(payload["message"])
                if name != "token":
                    continue
                if "llm_first_token" not in timings:
                    timings["llm_first_token"] = time.perf_counter()
                    print(
                        f"[voice_timing] llm_first_token={timings['llm_first_token'] - timings['request_received']:.3f}s",
                        flush=True,
                    )
                buffer += payload["text"]
                while True:
                    chunk, buffer = _take_semantic_chunk(buffer)
                    if not chunk:
                        break
                    if "first_semantic_chunk" not in timings:
                        timings["first_semantic_chunk"] = time.perf_counter()
                        print(
                            f"[voice_timing] first_semantic={timings['first_semantic_chunk'] - timings['request_received']:.3f}s",
                            flush=True,
                        )
                    await chunks.put(chunk)
            if buffer.strip():
                if "first_semantic_chunk" not in timings:
                    timings["first_semantic_chunk"] = time.perf_counter()
                    print(
                        f"[voice_timing] first_semantic={timings['first_semantic_chunk'] - timings['request_received']:.3f}s",
                        flush=True,
                    )
                await chunks.put(buffer.strip())
        except BaseException as exc:
            await chunks.put(exc)
        finally:
            await chunks.put(None)

    producer = asyncio.create_task(produce_text_chunks())
    try:
        while True:
            text_chunk = await chunks.get()
            if text_chunk is None:
                break
            if isinstance(text_chunk, BaseException):
                raise text_chunk
            timings.setdefault("tts_first_chunk_start", time.perf_counter())
            async for audio_chunk in _stream_tts_chunk(text_chunk):
                if "tts_first_audio" not in timings:
                    timings["tts_first_audio"] = time.perf_counter()
                    print(
                        f"[voice_timing] tts_first_audio={timings['tts_first_audio'] - timings['request_received']:.3f}s",
                        flush=True,
                    )
                timings["audio_chunks"] = timings.get("audio_chunks", 0) + 1
                yield audio_chunk
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


@app.post("/voice", response_class=StreamingResponse)
async def voice(request: Request) -> StreamingResponse:
    """Receive browser audio and return only the generated PCM audio stream."""
    started_at = time.perf_counter()
    timings: dict[str, float] = {"request_received": started_at}
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=422, detail="El audio está vacío.")
    if len(audio) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El audio supera el límite permitido.")
    try:
        timings["transcription_start"] = time.perf_counter()
        async with transcription_lock:
            prompt = await asyncio.to_thread(
                stt_provider.transcribe_audio,
                audio,
                request.headers.get("content-type", "audio/webm"),
            )
        timings["transcription_end"] = time.perf_counter()
        print(
            f"[voice_timing] transcription={timings['transcription_end'] - timings['transcription_start']:.3f}s",
            flush=True,
        )
        history_header = request.headers.get("X-Chat-History", "[]")
        history = json.loads(history_header)
        if not isinstance(history, list):
            history = []
        chain = get_model_chain()
        if not any(config.api_key for config in chain):
            raise HTTPException(status_code=503, detail="No hay API keys configuradas.")
        sample_rate = await asyncio.to_thread(tts_provider.sample_rate)
        timings["tts_model_ready"] = time.perf_counter()
    except HTTPException:
        raise
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="El servicio de voz no está instalado.") from exc
    except (ProviderError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="No se pudo procesar la conversación.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo procesar el audio.") from exc

    async def timed_stream():
        try:
            async for chunk in _voice_audio_stream(prompt, history or None, timings):
                yield chunk
        finally:
            timings["response_finished"] = time.perf_counter()
            elapsed = timings["response_finished"] - started_at
            logger.info(
                "voice_timing total=%.3fs transcribe=%.3fs llm_first_token=%.3fs "
                "first_semantic=%.3fs tts_model=%.3fs tts_first_audio=%.3fs "
                "audio_stream=%.3fs audio_chunks=%d",
                elapsed,
                timings.get("transcription_end", started_at) - timings.get("transcription_start", started_at),
                timings.get("llm_first_token", started_at) - started_at,
                timings.get("first_semantic_chunk", started_at) - started_at,
                timings.get("tts_model_ready", started_at) - started_at,
                timings.get("tts_first_audio", started_at) - started_at,
                timings["response_finished"] - timings.get("tts_first_audio", started_at),
                int(timings.get("audio_chunks", 0)),
            )
            print(
                "[voice_timing] "
                f"total={elapsed:.3f}s "
                f"transcribe={timings.get('transcription_end', started_at) - timings.get('transcription_start', started_at):.3f}s "
                f"llm_first_token={timings.get('llm_first_token', started_at) - started_at:.3f}s "
                f"first_semantic={timings.get('first_semantic_chunk', started_at) - started_at:.3f}s "
                f"tts_model={timings.get('tts_model_ready', started_at) - started_at:.3f}s "
                f"tts_first_audio={timings.get('tts_first_audio', started_at) - started_at:.3f}s "
                f"audio_chunks={int(timings.get('audio_chunks', 0))}",
                flush=True,
            )

    return StreamingResponse(
        timed_stream(),
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
):
    kwargs: dict[str, object] = {"messages": messages, "llm": llm_provider}
    if "tool_context" in inspect.signature(stream_agent).parameters:
        kwargs["tool_context"] = ToolContext(
            request_id=f"request-{uuid.uuid4()}",
            organization_id=str(organization_id) if organization_id else None,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=str(user_id) if user_id else None,
        )
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
        yield _event(name, payload)


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
        history = [
            {"role": message.role.value, "content": message.content}
            for message in persisted_messages
            if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
        ]
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


async def _speak_chunk(
    websocket: WebSocket,
    text: str,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    if tts_status != "ready" or not text.strip():
        return
    await _send_call_event(
        websocket,
        "tts.started",
        {"text": text},
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    sample_rate = await asyncio.to_thread(tts_provider.sample_rate)
    await websocket.send_json({"type": "tts.format", "sample_rate": sample_rate})
    async for audio_chunk in _stream_tts_chunk(text):
        await websocket.send_bytes(audio_chunk)
    await _send_call_event(
        websocket,
        "tts.completed",
        {},
        organization_id=organization_id,
        conversation_id=conversation_id,
    )


async def _run_call_turn(
    websocket: WebSocket,
    prompt: str,
    history: list[dict[str, str]],
    *,
    session: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> None:
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
        return
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
    pending: asyncio.Queue[str | None] = asyncio.Queue()

    async def speak_worker() -> None:
        while True:
            text = await pending.get()
            if text is None:
                return
            await _speak_chunk(
                websocket,
                text,
                organization_id=organization_id,
                conversation_id=conversation_id,
            )

    speaker = asyncio.create_task(speak_worker())
    try:
        async for name, payload in _agent_stream(
            prompt,
            messages=history or None,
            organization_id=organization_id,
            conversation_id=conversation_id,
            user_id=user_id,
        ):
            if name == "error":
                failed = True
                await _send_call_event(
                    websocket,
                    "error",
                    {"message": payload["message"]},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                break
            if name in {"tool.started", "tool.completed", "rag.started", "rag.completed"}:
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
                await pending.put(chunk)
        if buffer.strip():
            await pending.put(buffer.strip())
        await pending.put(None)
        await speaker
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
                return
            history.extend(
                [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
            )
            del history[:-40]
        await _send_call_event(
            websocket,
            "turn.completed",
            {"text": answer},
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
    except asyncio.CancelledError:
        speaker.cancel()
        try:
            await speaker
        except asyncio.CancelledError:
            pass
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
        speaker.cancel()
        try:
            await speaker
        except (asyncio.CancelledError, Exception):
            pass
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
            )
            session.add(call_record)
            await session.commit()
        except Exception:
            logger.exception("Call conversation lookup failed")
            await websocket.close(code=1011)
            return
        history: list[dict[str, str]] = [
            {"role": message.role.value, "content": message.content}
            for message in persisted_messages
            if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
        ]
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
        barge_hits = 0
        turn_task: asyncio.Task[None] | None = None
        stream = await asyncio.to_thread(stt_provider.create_stream)

        async def _reap_turn() -> None:
            nonlocal turn_task, last_partial, last_voice_at, first_voice_at, ignore_until, barge_hits, stream
            if turn_task is None or not turn_task.done():
                return
            task = turn_task
            turn_task = None
            barge_hits = 0
            last_partial = ""
            last_voice_at = 0.0
            first_voice_at = 0.0
            ignore_until = time.monotonic() + CALL_POST_TURN_GUARD_SECONDS
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Call turn failed")
            if stream is not None:
                await asyncio.to_thread(stt_provider.reset_stream, stream)

        async def _start_turn(prompt: str) -> None:
            nonlocal turn_task, last_partial, last_voice_at, first_voice_at, stream, ignore_until
            flushed = ""
            if stream is not None:
                try:
                    flushed = await asyncio.to_thread(stt_provider.finish_stream, stream, sample_rate)
                except Exception:
                    logger.exception("Sherpa flush failed")
                stream = await asyncio.to_thread(stt_provider.create_stream)
            final = flushed.strip() if _usable_transcript(flushed) else prompt.strip()
            last_partial = ""
            last_voice_at = 0.0
            first_voice_at = 0.0
            if not _usable_transcript(final):
                ignore_until = 0.0
                return
            ignore_until = time.monotonic() + CALL_TURN_GUARD_SECONDS
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
                )
            )

        async def _barge_in() -> None:
            nonlocal turn_task, last_partial, last_voice_at, first_voice_at, ignore_until, barge_hits, stream
            barge_hits = 0
            if turn_task is None:
                return
            task = turn_task
            turn_task = None
            if not task.done():
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
            last_partial = ""
            last_voice_at = time.monotonic()
            first_voice_at = last_voice_at
            ignore_until = 0.0
            if stream is not None:
                await asyncio.to_thread(stt_provider.reset_stream, stream)

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()
                    try:
                        await turn_task
                    except (asyncio.CancelledError, Exception):
                        pass
                break
            raw = message.get("bytes")
            text = message.get("text")
            if raw is not None:
                if pcm_mode:
                    await _reap_turn()
                    level, voiced, rms = pcm_speech_features(raw, sample_rate)
                    await websocket.send_json({"type": "wave.level", "value": level, "source": "customer"})
                    now = time.monotonic()
                    busy = turn_task is not None and not turn_task.done()
                    speaking = voiced or rms >= CALL_SPEECH_RMS
                    if speaking and not busy and now >= ignore_until:
                        last_voice_at = now
                        if first_voice_at <= 0:
                            first_voice_at = now
                    if busy:
                        if voiced and rms >= CALL_BARGE_RMS and now >= ignore_until:
                            barge_hits += 1
                        else:
                            barge_hits = 0
                        if now >= ignore_until and (
                            barge_hits >= 6 or (voiced and rms >= CALL_BARGE_STRONG_RMS)
                        ):
                            barge_hits = 0
                            await _barge_in()
                        else:
                            continue
                    if stream is None or now < ignore_until:
                        continue
                    try:
                        partial, ended = await asyncio.to_thread(stt_provider.feed_pcm, stream, raw, sample_rate)
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
                    if partial and partial != last_partial:
                        last_partial = partial
                        if _usable_transcript(partial):
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
                await _send_call_event(
                    websocket,
                    "customer.transcript",
                    {"text": prompt},
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                await _run_call_turn(
                    websocket,
                    prompt,
                    history,
                    session=session,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    user_id=user.id,
                )
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
                rate = payload.get("sample_rate")
                if isinstance(rate, int) and rate > 0:
                    sample_rate = rate
                pcm_mode = True
                last_partial = ""
                last_voice_at = 0.0
                first_voice_at = 0.0
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
                if stream is not None:
                    await asyncio.to_thread(stt_provider.reset_stream, stream)
                continue
            if payload.get("type") == "barge":
                busy = turn_task is not None and not turn_task.done()
                if busy and time.monotonic() >= ignore_until:
                    await _barge_in()
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
            await _run_call_turn(
                websocket,
                prompt,
                history,
                session=session,
                organization_id=organization_id,
                conversation_id=conversation_id,
                user_id=user.id,
            )
    except WebSocketDisconnect:
        logger.info("Call WebSocket disconnected")
    except Exception:
        call_completion_status = CallStatus.FAILED
        logger.exception("Call WebSocket failed")
    finally:
        if call_record is not None:
            await _finish_call(
                session,
                call_record,
                organization_id=call_record.organization_id,
                conversation_id=call_record.conversation_id,
                status=call_completion_status,
            )
