from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

from ..features.transcription import service as sherpa


class CallRecognizer:
    """One Sherpa stream and one queue, isolated from every other call."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.inbox: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._lock = threading.Lock()

    async def feed(self, pcm: bytes) -> tuple[str, bool]:
        def _once() -> tuple[str, bool]:
            with self._lock:
                if self.inbox.full():
                    try:
                        self.inbox.get_nowait()
                    except queue.Empty:
                        pass
                self.inbox.put_nowait(pcm)
                return sherpa.feed_pcm(self.stream, pcm, 16000)

        return await asyncio.to_thread(_once)

    async def finish(self) -> str:
        def _finish() -> str:
            with self._lock:
                return sherpa.finish_stream(self.stream, 16000)

        return await asyncio.to_thread(_finish)

    def close(self) -> None:
        return None


class SherpaSTTProvider:
    """Shared recognizer. ``clone()`` returns a private stream and queue."""

    def preload(self) -> None:
        sherpa.preload_model()

    def clone(self) -> CallRecognizer:
        return CallRecognizer(sherpa.create_stream())
