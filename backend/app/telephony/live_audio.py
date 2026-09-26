from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class LiveAudioHub:
    """Per-viewer bounded audio queues. A slow viewer never blocks the caller."""

    def __init__(self, maxsize: int = 24) -> None:
        self._maxsize = maxsize
        self._viewers: dict[tuple[str, str], list[asyncio.Queue[bytes]]] = defaultdict(list)

    def subscribe(self, organization_id: object, call_id: object) -> asyncio.Queue[bytes]:
        viewer: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self._maxsize)
        self._viewers[(str(organization_id), str(call_id))].append(viewer)
        return viewer

    def unsubscribe(self, organization_id: object, call_id: object, viewer: asyncio.Queue[bytes]) -> None:
        key = (str(organization_id), str(call_id))
        viewers = self._viewers.get(key)
        if viewers is None:
            return
        if viewer in viewers:
            viewers.remove(viewer)
        if not viewers:
            self._viewers.pop(key, None)

    def publish(self, organization_id: object, call_id: object, frame: bytes) -> None:
        viewers = self._viewers.get((str(organization_id), str(call_id)))
        if not viewers:
            return
        for viewer in tuple(viewers):
            if viewer.full():
                try:
                    viewer.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                viewer.put_nowait(frame)
            except asyncio.QueueFull:
                continue

    def viewer_count(self, organization_id: object | None = None, call_id: object | None = None) -> int:
        if organization_id is None:
            return sum(len(viewers) for viewers in self._viewers.values())
        return len(self._viewers.get((str(organization_id), str(call_id)), []))


live_audio_hub = LiveAudioHub()


class CallMonitorHub:
    def __init__(self, maxsize: int = 64) -> None:
        self._maxsize = maxsize
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    def subscribe(self, organization_id: object, call_id: object) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers[(str(organization_id), str(call_id))].append(queue)
        return queue

    def unsubscribe(self, organization_id: object, call_id: object, queue: asyncio.Queue[dict[str, Any]]) -> None:
        key = (str(organization_id), str(call_id))
        subscribers = self._subscribers.get(key)
        if subscribers is None:
            return
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(key, None)

    def publish(self, organization_id: object, call_id: object, message: dict[str, Any]) -> None:
        subscribers = self._subscribers.get((str(organization_id), str(call_id)))
        if not subscribers:
            return
        for subscriber in tuple(subscribers):
            if subscriber.full():
                try:
                    subscriber.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                subscriber.put_nowait(message)
            except asyncio.QueueFull:
                continue


monitor_hub = CallMonitorHub()
