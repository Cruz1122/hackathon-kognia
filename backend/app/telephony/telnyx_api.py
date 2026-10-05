from __future__ import annotations

import logging
from typing import Any

import httpx

from vendor.patter.transport import stream_url

from .settings import TelnyxSettings

logger = logging.getLogger("hackathon.telnyx.api")
API_ROOT = "https://api.telnyx.com"


class TelnyxApi:
    def __init__(self, settings: TelnyxSettings, transport: Any | None = None) -> None:
        self.settings = settings
        self._transport = transport

    async def answer(self, call_control_id: str) -> None:
        await self._post(f"/v2/calls/{call_control_id}/actions/answer", {})

    async def dial(self, to: str, *, command_id: str, client_state: str) -> dict:
        result = await self._send('POST', '/v2/calls', {
            'to': to, 'from': self.settings.phone_number, 'connection_id': self.settings.connection_id,
            'command_id': command_id, 'client_state': client_state,
        })
        return result['data']

    async def streaming_start(self, call_control_id: str, media_url: str) -> None:
        path = f"/v2/calls/{call_control_id}/actions/streaming_start"
        negotiated = {
            "stream_url": media_url,
            "stream_track": "inbound_track",
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "L16",
            "stream_bidirectional_sampling_rate": 16000,
            "stream_codec": "L16",
        }
        try:
            await self._post(path, negotiated)
        except Exception:
            logger.warning("L16 streaming_start failed; retrying with the carrier default")
            await self._post(
                path,
                {"stream_url": media_url, "stream_track": "inbound_track"},
            )

    async def record_start(self, call_control_id: str) -> None:
        path = f"/v2/calls/{call_control_id}/actions/record_start"
        requested = {
            "format": "wav",
            "channels": "dual",
            "recording_track": "both",
            "trim": "disabled",
            "play_beep": False,
        }
        try:
            await self._post(path, requested)
        except Exception:
            logger.warning("Telnyx record_start rejected trim; retrying without it")
            requested.pop("trim")
            await self._post(path, requested)

    async def hangup(self, call_control_id: str, *, command_id: str) -> None:
        await self._post(
            f"/v2/calls/{call_control_id}/actions/hangup",
            {"command_id": command_id},
        )

    async def sync_webhook(self) -> None:
        if not self.settings.connection_id or not self.settings.webhook_host:
            return
        await self._patch(
            f"/v2/call_control_applications/{self.settings.connection_id}",
            {
                "webhook_event_url": f"https://{self.settings.webhook_host}/webhooks/telnyx/voice",
                "webhook_api_version": "2",
            },
        )

    def media_url(self, call_id: str, token: str) -> str:
        return stream_url(self.settings.webhook_host, call_id, token)

    async def _post(self, path: str, body: dict[str, Any]) -> None:
        await self._send("POST", path, body)

    async def _patch(self, path: str, body: dict[str, Any]) -> None:
        await self._send("PATCH", path, body)

    async def _send(self, method: str, path: str, body: dict[str, Any]) -> Any:
        if self._transport is not None:
            return await self._transport(method, path, body)
        if not self.settings.api_key:
            raise RuntimeError("Telnyx API key is missing")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(
                method,
                API_ROOT + path,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json=body,
            )
            response.raise_for_status()
            logger.info("Telnyx %s %s -> %s", method, path.split("/actions/")[-1], response.status_code)
            return response.json() if response.content else {}
