"""Telnyx stream URL helper forked from Patter.

Upstream builds ``wss://{host}/ws/telnyx/stream/{call_id}`` and puts the media
token in the query string. This fork keeps that shape and leaves STT, TTS, and
the agent loop to the host application.
"""

from __future__ import annotations

from urllib.parse import quote

STREAM_TOKEN_PARAM = "token"


def stream_url(webhook_host: str, call_id: str, token: str) -> str:
    host = webhook_host.removeprefix("https://").removeprefix("http://").strip("/")
    return f"wss://{host}/ws/telnyx/stream/{quote(call_id, safe='')}?{STREAM_TOKEN_PARAM}={quote(token, safe='')}"
