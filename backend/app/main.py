import asyncio
import json
import logging
import os
import queue
import re
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import AppEnv, Provider, get_app_env, get_model_chain
from .features.agent.service import stream_agent
from .features.chat.schemas import AskRequest
from .features.transcription.schemas import TranscriptionResponse
from .features.transcription.service import (
    create_stream,
    feed_pcm,
    finish_stream,
    pcm_wave_level,
    preload_model,
    reset_stream,
    transcribe_audio,
)
from .features.synthesis.schemas import SynthesisRequest
from .features.synthesis.service import (
    pocket_sample_rate,
    preload_pocket_tts,
    stream_pocket_audio,
    synthesize_text,
)
from .providers import ProviderError, stream_provider


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

transcription_lock = asyncio.Semaphore(1)
tts_status = "starting"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm Sherpa-ONNX before serving requests without blocking the event loop."""
    try:
        await asyncio.to_thread(preload_model)
    except ImportError:
        # Keep the API available for text-only fallback when dependencies are absent.
        pass
    global tts_status
    logger.info("Loading Pocket TTS weights before accepting requests")
    try:
        await asyncio.to_thread(preload_pocket_tts)
        tts_status = "ready"
        logger.info("Pocket TTS is ready")
    except Exception:
        tts_status = "error"
        logger.exception("Pocket TTS failed to load during backend startup")
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
    return {"status": "ok", "tts": tts_status}


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
        raise HTTPException(status_code=503, detail="Sherpa-ONNX no está instalado.") from exc
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


@app.post("/synthesize/stream", response_class=StreamingResponse)
async def synthesize_stream(request: SynthesisRequest) -> StreamingResponse:
    """Stream Pocket TTS as mono signed-int16 PCM, using one model worker."""
    try:
        sample_rate = await asyncio.to_thread(pocket_sample_rate)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Pocket TTS no está instalado.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo cargar Pocket TTS.") from exc
    return StreamingResponse(
        stream_pocket_audio(request.text),
        media_type=f"audio/L16; rate={sample_rate}; channels=1",
        headers={
            "X-Audio-Sample-Rate": str(sample_rate),
            "X-Audio-Encoding": "signed-int16-le",
        },
    )


def _take_semantic_chunk(buffer: str, flush: bool = False) -> tuple[str, str]:
    words = buffer.strip().split()
    if flush:
        return buffer.strip(), ""
    sentence = re.search(r"[.!?](?:[\"'»”)]*)?(?=\s|$)", buffer)
    if sentence and len(buffer[: sentence.end()].split()) >= 3:
        return buffer[: sentence.end()].strip(), buffer[sentence.end() :].lstrip()
    clause = re.search(r"[;:](?:\s|$)", buffer)
    if clause and len(buffer[: clause.end()].split()) >= 8:
        return buffer[: clause.end()].strip(), buffer[clause.end() :].lstrip()
    if len(words) >= 20:
        match = list(re.finditer(r"\S+", buffer))[19]
        return buffer[: match.end()].strip(), buffer[match.end() :].lstrip()
    return "", buffer


async def _stream_tts_chunk(text: str):
    """Bridge the blocking Pocket generator without buffering its audio."""
    audio_queue: queue.Queue[bytes | BaseException | None] = queue.Queue()

    def generate() -> None:
        try:
            for chunk in stream_pocket_audio(text):
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
                provider_stream=stream_provider,
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
                transcribe_audio,
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
        sample_rate = await asyncio.to_thread(pocket_sample_rate)
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


_TRANSCRIPT_LETTER = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")


def _usable_transcript(text: str) -> bool:
    return len(_TRANSCRIPT_LETTER.findall(text)) >= 2


async def _speak_chunk(websocket: WebSocket, text: str) -> None:
    if tts_status != "ready" or not text.strip():
        return
    await websocket.send_json({"type": "tts.started", "text": text})
    sample_rate = await asyncio.to_thread(pocket_sample_rate)
    await websocket.send_json({"type": "tts.format", "sample_rate": sample_rate})
    async for audio_chunk in _stream_tts_chunk(text):
        await websocket.send_bytes(audio_chunk)
    await websocket.send_json({"type": "tts.completed"})


async def _run_call_turn(
    websocket: WebSocket,
    prompt: str,
    history: list[dict[str, str]],
) -> None:
    await websocket.send_json({"type": "turn.started"})
    answer = ""
    buffer = ""
    failed = False
    try:
        async for name, payload in stream_agent(
            prompt,
            messages=history or None,
            provider_stream=stream_provider,
        ):
            if name == "error":
                failed = True
                await websocket.send_json({"type": "error", "message": payload["message"]})
                break
            if name == "tool.started" or name == "tool.completed":
                await websocket.send_json({"type": name, **payload})
                continue
            if name != "token":
                continue
            token = payload["text"]
            answer += token
            buffer += token
            await websocket.send_json({"type": "agent.token", "text": token})
            chunk, buffer = _take_semantic_chunk(buffer)
            if chunk:
                await _speak_chunk(websocket, chunk)
        if buffer.strip():
            await _speak_chunk(websocket, buffer.strip())
        if not failed and answer.strip():
            history.extend(
                [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
            )
            del history[:-40]
        await websocket.send_json({"type": "turn.completed", "text": answer})
    except asyncio.CancelledError:
        try:
            await websocket.send_json({"type": "turn.cancelled"})
        except Exception:
            pass
        raise


@app.websocket("/ws/call")
async def call_socket(websocket: WebSocket) -> None:
    """Live PCM: Sherpa partials, agent tokens and TTS."""
    await websocket.accept()
    await websocket.send_json({"type": "call.connected", "tts": tts_status})
    history: list[dict[str, str]] = []
    audio_mime = "audio/webm"
    pcm_mode = True
    sample_rate = 16000
    stream = None
    last_partial = ""
    last_voice_at = 0.0
    ignore_until = 0.0
    barge_hits = 0
    turn_task: asyncio.Task[None] | None = None
    try:
        stream = await asyncio.to_thread(create_stream)

        async def _reap_turn() -> None:
            nonlocal turn_task, last_partial, last_voice_at, ignore_until, barge_hits, stream
            if turn_task is None or not turn_task.done():
                return
            task = turn_task
            turn_task = None
            barge_hits = 0
            last_partial = ""
            last_voice_at = 0.0
            ignore_until = time.monotonic() + 0.4
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Call turn failed")
            if stream is not None:
                await asyncio.to_thread(reset_stream, stream)

        async def _start_turn(prompt: str) -> None:
            nonlocal turn_task, last_partial, last_voice_at, stream
            flushed = ""
            if stream is not None:
                try:
                    flushed = await asyncio.to_thread(finish_stream, stream, sample_rate)
                except Exception:
                    logger.exception("Sherpa flush failed")
                stream = await asyncio.to_thread(create_stream)
            final = flushed.strip() if _usable_transcript(flushed) else prompt
            last_partial = ""
            last_voice_at = 0.0
            await websocket.send_json({"type": "customer.transcript", "text": final})
            turn_task = asyncio.create_task(_run_call_turn(websocket, final, history))

        async def _barge_in() -> None:
            nonlocal turn_task, barge_hits, last_partial, last_voice_at, ignore_until
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
            ignore_until = 0.0

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
                    level = pcm_wave_level(raw, sample_rate)
                    await websocket.send_json({"type": "wave.level", "value": level, "source": "customer"})
                    now = time.monotonic()
                    busy = turn_task is not None and not turn_task.done()
                    speaking = level >= 0.09
                    if speaking:
                        last_voice_at = now
                    if busy:
                        barge_hits = barge_hits + 1 if level >= 0.16 else 0
                        if barge_hits >= 3 and now >= ignore_until:
                            await _barge_in()
                        continue
                    if stream is None or now < ignore_until:
                        continue
                    try:
                        partial, _ended = await asyncio.to_thread(feed_pcm, stream, raw, sample_rate)
                    except Exception:
                        logger.exception("Call transcription failed")
                        await websocket.send_json(
                            {"type": "error", "message": "No se pudo transcribir el audio."}
                        )
                        continue
                    if partial and partial != last_partial:
                        last_partial = partial
                        if _usable_transcript(partial):
                            await websocket.send_json({"type": "customer.partial", "text": partial})
                    prompt = last_partial.strip()
                    if (
                        not prompt
                        or not _usable_transcript(prompt)
                        or last_voice_at <= 0
                        or now - last_voice_at < 1.0
                    ):
                        continue
                    await _start_turn(prompt)
                    continue
                try:
                    async with transcription_lock:
                        prompt = await asyncio.to_thread(transcribe_audio, raw, audio_mime)
                except Exception:
                    logger.exception("Call transcription failed")
                    await websocket.send_json(
                        {"type": "error", "message": "No se pudo transcribir el audio."}
                    )
                    continue
                if not prompt.strip():
                    await websocket.send_json({"type": "transcript.empty"})
                    continue
                await websocket.send_json({"type": "customer.transcript", "text": prompt})
                await _run_call_turn(websocket, prompt, history)
                continue
            if not text:
                await websocket.send_json({"type": "error", "message": "Mensaje de llamada inválido."})
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Mensaje de llamada inválido."})
                continue
            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "message": "Mensaje de llamada inválido."})
                continue
            if payload.get("type") == "pcm.start":
                rate = payload.get("sample_rate")
                if isinstance(rate, int) and rate > 0:
                    sample_rate = rate
                pcm_mode = True
                last_partial = ""
                last_voice_at = 0.0
                if stream is not None:
                    await asyncio.to_thread(reset_stream, stream)
                else:
                    stream = await asyncio.to_thread(create_stream)
                continue
            if payload.get("type") == "pcm.stop":
                pcm_mode = False
                last_partial = ""
                last_voice_at = 0.0
                if stream is not None:
                    await asyncio.to_thread(reset_stream, stream)
                continue
            if payload.get("type") == "audio":
                mime = payload.get("mime")
                if isinstance(mime, str) and mime.strip():
                    audio_mime = mime
                continue
            if payload.get("type") != "turn":
                await websocket.send_json({"type": "error", "message": "Mensaje de llamada inválido."})
                continue
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                await websocket.send_json({"type": "error", "message": "El mensaje está vacío."})
                continue
            incoming = payload.get("messages", [])
            if isinstance(incoming, list) and incoming:
                history = [
                    message
                    for message in incoming
                    if isinstance(message, dict)
                    and message.get("role") in {"user", "assistant"}
                    and isinstance(message.get("content"), str)
                ]
            await _run_call_turn(websocket, prompt, history)
    except WebSocketDisconnect:
        logger.info("Call WebSocket disconnected")
    except Exception:
        logger.exception("Call WebSocket failed")
