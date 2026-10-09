from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import wave
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from urllib.parse import quote, urlencode

SAMPLE_RATE = 16000

Connect = Callable[[], Awaitable[object]]


class SynthesisNotConfigured(RuntimeError):
    """ElevenLabs credentials are missing."""


def speech_text(text: str) -> str:
    """Keep the written transcript intact while giving the voice a Spanish cue."""
    return re.sub(r"(?i)\bwane\b", "Güein", text)


def _ws_base(base: str) -> str:
    trimmed = base.strip().rstrip("/")
    if trimmed.startswith("https://"):
        return "wss://" + trimmed[len("https://") :]
    if trimmed.startswith("http://"):
        return "ws://" + trimmed[len("http://") :]
    return trimmed


def settings() -> tuple[str, str, str, str]:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    model = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5").strip() or "eleven_flash_v2_5"
    base = _ws_base(os.getenv("ELEVENLABS_BASE_URL", "wss://api.elevenlabs.io"))
    if not key or not voice_id:
        raise SynthesisNotConfigured("Faltan ELEVENLABS_API_KEY o ELEVENLABS_VOICE_ID.")
    return key, voice_id, model, base or "wss://api.elevenlabs.io"


def stream_url(voice_id: str, model: str, base: str) -> str:
    query = urlencode(
        {
            "model_id": model,
            "language_code": "es",
            "output_format": "pcm_16000",
        }
    )
    return f"{base}/v1/text-to-speech/{quote(voice_id, safe='')}/stream-input?{query}"


def even_pcm(pending: bytes, incoming: bytes) -> tuple[bytes, bytes]:
    """Return complete int16 samples and keep a trailing odd byte."""
    data = pending + incoming
    even = len(data) - (len(data) % 2)
    return data[:even], data[even:]


class ElevenLabsTurn:
    """One realtime TTS socket for a single agent turn."""

    sample_rate = SAMPLE_RATE

    def __init__(self, connect: Connect | None = None) -> None:
        self._connect = connect
        self._ws: object | None = None
        self._reader: asyncio.Task[None] | None = None
        self._audio: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        self._tail = b""
        self._cancelled = False
        self._ended = False
        self._keepalive: asyncio.Task[None] | None = None

    async def open(self) -> None:
        if self._connect is not None:
            self._ws = await self._connect()
        else:
            key, voice_id, model, base = settings()
            self._ws = await _connect_elevenlabs(stream_url(voice_id, model, base), key)
        self._reader = asyncio.create_task(self._read())
        self._keepalive = asyncio.create_task(self._keep_alive())
        await self._send({"text": " "})

    async def say(self, text: str) -> None:
        spoken = speech_text(text).strip()
        if not spoken or self._cancelled or self._ws is None:
            return
        await self._send({"text": f"{spoken} ", "flush": True})

    async def finish(self) -> None:
        """Close the text stream and let the server finish audio already requested."""
        if self._cancelled or self._ws is None:
            return
        await self._stop_keepalive()
        await self._send({"text": ""})

    async def cancel(self) -> None:
        """Drop the socket without asking the server to generate leftover text."""
        self._cancelled = True
        await self._stop_keepalive()
        await self._close_socket()
        reader = self._reader
        if reader is not None and not reader.done():
            reader.cancel()
        if reader is not None:
            await asyncio.gather(reader, return_exceptions=True)
        self._end()

    async def audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._audio.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            if item:
                yield item

    async def _keep_alive(self) -> None:
        """ElevenLabs closes the socket after 20s without text. A space keeps it open."""
        try:
            while not self._cancelled and not self._ended:
                await asyncio.sleep(10)
                if self._cancelled or self._ended or self._ws is None:
                    return
                await self._send({"text": " "})
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _stop_keepalive(self) -> None:
        task = self._keepalive
        self._keepalive = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _send(self, payload: dict[str, object]) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("La conexión de voz no está abierta.")
        await ws.send(json.dumps(payload))  # type: ignore[attr-defined]

    async def _read(self) -> None:
        ws = self._ws
        if ws is None:
            self._end()
            return
        try:
            async for raw in ws:  # type: ignore[attr-defined]
                if self._cancelled:
                    break
                self._accept(raw)
                if self._ended:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._cancelled:
                self._fail(exc)
        finally:
            self._end()

    def _accept(self, raw: object) -> None:
        if self._cancelled or self._ended:
            return
        if not isinstance(raw, str):
            raw = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._fail(exc)
            return
        if not isinstance(payload, dict):
            return
        if payload.get("isFinal") is True:
            self._tail = b""
            self._end()
            return
        error = payload.get("error") or payload.get("detail")
        if error and not payload.get("audio"):
            self._fail(RuntimeError(str(error)))
            return
        audio = payload.get("audio")
        if not audio or not isinstance(audio, str):
            return
        try:
            pcm = base64.b64decode(audio)
        except Exception as exc:
            self._fail(exc)
            return
        ready, self._tail = even_pcm(self._tail, pcm)
        if ready:
            self._audio.put_nowait(ready)

    def _fail(self, exc: BaseException) -> None:
        if self._ended:
            return
        self._ended = True
        self._tail = b""
        self._audio.put_nowait(exc)

    def _end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._tail = b""
        self._audio.put_nowait(None)

    async def _close_socket(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        close = getattr(ws, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception:
            return


class SyncSpeechTurn:
    """Speak a voice that only exposes a blocking stream_audio(), one sentence at a time."""

    def __init__(self, voice: object) -> None:
        self._voice = voice
        self.sample_rate = int(voice.sample_rate())  # type: ignore[attr-defined]
        self._text: asyncio.Queue[str | None] = asyncio.Queue()
        self._audio: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._cancelled = False

    async def open(self) -> None:
        self._worker = asyncio.create_task(self._run())

    async def say(self, text: str) -> None:
        if text.strip() and not self._cancelled:
            await self._text.put(text)

    async def finish(self) -> None:
        await self._text.put(None)
        if self._worker is not None:
            await self._worker

    async def cancel(self) -> None:
        self._cancelled = True
        if self._worker is not None:
            self._worker.cancel()
        self._audio.put_nowait(None)

    async def audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._audio.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            if item:
                yield item

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not self._cancelled:
                text = await self._text.get()
                if text is None:
                    break
                await asyncio.to_thread(self._produce, text, loop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._audio.put_nowait(exc)
        finally:
            self._audio.put_nowait(None)

    def _produce(self, text: str, loop: asyncio.AbstractEventLoop) -> None:
        for chunk in self._voice.stream_audio(text):  # type: ignore[attr-defined]
            if self._cancelled or not chunk:
                return
            asyncio.run_coroutine_threadsafe(self._audio.put(chunk), loop).result()


def open_speech_turn(voice: object) -> ElevenLabsTurn | SyncSpeechTurn:
    opener = getattr(voice, "open_turn", None)
    if opener is not None:
        return opener()
    return SyncSpeechTurn(voice)


def preload_tts() -> None:
    """Fail fast when the voice credentials are absent. The socket opens per turn."""
    settings()


def tts_sample_rate() -> int:
    return SAMPLE_RATE


def stream_tts_audio(text: str) -> Iterator[bytes]:
    """Yield PCM for one complete phrase. Used by one-shot routes, not live turns."""

    async def _collect() -> list[bytes]:
        turn = ElevenLabsTurn()
        await turn.open()
        try:
            await turn.say(text)
            await turn.finish()
            return [chunk async for chunk in turn.audio()]
        finally:
            await turn.cancel()

    return iter(asyncio.run(_collect()))


def synthesize_text(text: str) -> bytes:
    """Generate a mono WAV using the streaming voice."""
    return _wav_bytes(b"".join(stream_tts_audio(text)), SAMPLE_RATE)


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def _connect_elevenlabs(url: str, key: str) -> object:
    import websockets

    headers = {"xi-api-key": key}
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)
