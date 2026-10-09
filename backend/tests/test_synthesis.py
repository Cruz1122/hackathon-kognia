import asyncio
import base64
import json

import pytest

from app import main
from app.features.synthesis import service
from app.features.synthesis.service import ElevenLabsTurn, SynthesisNotConfigured


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if payload.get("flush"):
            await self._incoming.put(json.dumps({"audio": base64.b64encode(b"\x01").decode()}))
            await self._incoming.put(json.dumps({"audio": base64.b64encode(b"\x02\x03\x04").decode()}))
        if payload.get("text") == "":
            await self._incoming.put(json.dumps({"isFinal": True}))

    async def close(self) -> None:
        self.closed = True
        await self._incoming.put(None)

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str:
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_even_pcm_keeps_a_split_sample() -> None:
    ready, pending = service.even_pcm(b"", b"\x01")
    assert ready == b""
    assert pending == b"\x01"
    ready, pending = service.even_pcm(pending, b"\x02\x03")
    assert ready == b"\x01\x02"
    assert pending == b"\x03"


def test_preload_requires_elevenlabs_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(SynthesisNotConfigured):
        service.preload_tts()


def test_semantic_chunker_does_not_cut_an_unfinished_spanish_sentence() -> None:
    unfinished = "Claro puedo ayudarte a revisar el problema paso"
    complete = f"{unfinished} a paso."

    assert main._take_semantic_chunk(unfinished) == ("", unfinished)
    assert main._take_semantic_chunk(complete) == (complete, "")


@pytest.mark.asyncio
async def test_turn_flushes_each_sentence_and_rejoins_split_pcm() -> None:
    socket = FakeSocket()

    async def connect() -> FakeSocket:
        return socket

    turn = ElevenLabsTurn(connect)
    await turn.open()
    heard: list[bytes] = []

    async def collect() -> None:
        async for chunk in turn.audio():
            heard.append(chunk)

    reader = asyncio.create_task(collect())
    await turn.say("Soy Wane. Sigo aquí.")
    await turn.finish()
    await reader

    assert socket.sent[0] == {"text": " "}
    assert socket.sent[1] == {"text": "Soy Güein. Sigo aquí. ", "flush": True}
    assert socket.sent[2] == {"text": ""}
    assert heard == [b"\x01\x02\x03\x04"]
    await turn.cancel()


@pytest.mark.asyncio
async def test_turn_cancel_closes_without_flushing_the_buffer() -> None:
    socket = FakeSocket()

    async def connect() -> FakeSocket:
        return socket

    turn = ElevenLabsTurn(connect)
    await turn.open()
    await turn.say("Hola, ¿cómo te llamas?")
    await turn.cancel()

    assert socket.closed is True
    assert all(message.get("text") != "" for message in socket.sent)
