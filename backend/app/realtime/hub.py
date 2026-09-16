from __future__ import annotations

from collections import defaultdict
from collections.abc import MutableMapping
from typing import Any

from .events import RealtimeEvent


class RealtimeHub:
    """Small single-process fan-out hub partitioned by organization."""

    def __init__(self) -> None:
        self._connections: MutableMapping[str, set[Any]] = defaultdict(set)

    def connect(self, websocket: Any, organization_id: object) -> None:
        """Register an already-accepted JSON WebSocket for one organization."""
        self._connections[str(organization_id)].add(websocket)

    def disconnect(self, websocket: Any, organization_id: object | None = None) -> None:
        """Remove a connection, pruning empty organization buckets."""
        organization_keys = (
            [str(organization_id)] if organization_id is not None else list(self._connections)
        )
        for organization_key in organization_keys:
            connections = self._connections.get(organization_key)
            if connections is None:
                continue
            connections.discard(websocket)
            if not connections:
                self._connections.pop(organization_key, None)

    async def publish(self, event: RealtimeEvent) -> int:
        """Send an event to the event's organization and clean dead subscribers.

        The hub deliberately calls only ``send_json``. Media transport remains a
        responsibility of the originating call WebSocket.
        """
        organization_key = str(event.organization_id)
        connections = self._connections.get(organization_key)
        if not connections:
            return 0

        message = event.model_dump(mode="json")
        dead: list[Any] = []
        delivered = 0
        for websocket in tuple(connections):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
            else:
                delivered += 1
        for websocket in dead:
            self.disconnect(websocket, organization_key)
        return delivered

    def connection_count(self, organization_id: object | None = None) -> int:
        """Expose a small diagnostic useful for tests and local health checks."""
        if organization_id is not None:
            return len(self._connections.get(str(organization_id), set()))
        return sum(len(connections) for connections in self._connections.values())
