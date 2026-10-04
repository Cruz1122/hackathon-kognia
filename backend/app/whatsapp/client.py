from __future__ import annotations

import hashlib
from urllib.parse import urlparse

import httpx

from ..agent.settings import GRAPH_VERSION, settings

MAX_AUDIO_BYTES = 16 * 1024 * 1024


class WhatsAppClient:
    def __init__(self, transport=None):
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False,
            headers={'Authorization': f'Bearer {settings().whatsapp_token}'})

    async def send(self, payload: dict) -> str:
        async with self.client() as client:
            response = await client.post(
                f'https://graph.facebook.com/{GRAPH_VERSION}/{settings().whatsapp_number}/messages',
                json={'messaging_product': 'whatsapp', **payload})
            response.raise_for_status()
            return str(response.json()['messages'][0]['id'])

    async def audio(self, media_id: str) -> tuple[bytes, str]:
        if not media_id.isdigit():
            raise ValueError('Invalid media ID')
        async with self.client() as client:
            response = await client.get(f'https://graph.facebook.com/{GRAPH_VERSION}/{media_id}',
                params={'phone_number_id': settings().whatsapp_number})
            response.raise_for_status()
            metadata = response.json()
            mime = str(metadata['mime_type'])
            if not mime.startswith('audio/') or int(metadata['file_size']) > MAX_AUDIO_BYTES:
                raise ValueError('Unsupported or oversized audio')
            url = urlparse(metadata['url'])
            if (url.scheme != 'https' or url.hostname != 'lookaside.fbsbx.com'
                    or url.username or url.password or url.port not in (None, 443)):
                raise ValueError('Untrusted media host')
            data = bytearray()
            async with client.stream('GET', metadata['url']) as download:
                download.raise_for_status()
                async for chunk in download.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_AUDIO_BYTES:
                        raise ValueError('Audio too large')
            if hashlib.sha256(data).hexdigest() != metadata['sha256']:
                raise ValueError('Audio checksum mismatch')
            return bytes(data), mime
