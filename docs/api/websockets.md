# WebSockets

**Estado:** verificado contra `call_socket` y `events_socket` en `backend/app/main.py`.

Códigos de cierre usados por la API (además de 1000/1011):

| Código | Significado |
| --- | --- |
| 4400 | Attach inválido (`conversation.attach` mal formado) |
| 4401 | Auth ausente o token inválido |
| 4403 | SUPERADMIN sin org, o conversación fuera de tenant |
| 1003 | `/ws/events` recibió binario |

## `/ws/call`

Handshake obligatorio, en este orden, **antes** de audio o comandos:

```json
{"type":"auth","token":"<jwt>"}
{"type":"conversation.attach","conversation_id":"<uuid>"}
```

El servidor responde:

```json
{"type":"call.connected","tts":"ready|error|starting","conversation_id":"..."}
{"type":"tts.format","sample_rate": <int>}
```

### Cliente → servidor

| Tipo | Payload | Notas |
| --- | --- | --- |
| binario | PCM s16le | Modo PCM (default). 16 kHz en la demo |
| `pcm.start` | `{ sample_rate? }` | Activa PCM y resetea Sherpa |
| `pcm.stop` | — | Sale de modo PCM |
| `barge` | — | Cancela el turno TTS en curso |
| `audio` | `{ mime }` | MIME para el camino no-PCM |
| `turn` | `{ prompt }` | Turno de texto sin STT |

### Servidor → cliente (JSON directo)

Estos van por el socket de llamada. Muchos se duplican al hub (ver tabla).

| `type` | Contenido típico | Hub |
| --- | --- | --- |
| `wave.level` | `{ value, source: "customer" }` | No (solo WS llamada) |
| `customer.partial` | `{ text }` | Sí |
| `customer.transcript` | `{ text }` | Sí |
| `turn.started` | `{}` | Sí |
| `agent.token` | `{ text }` | Sí |
| `tool.started` / `tool.completed` | tool metadata | Sí |
| `rag.started` / `rag.completed` | `{ used_rag: true, message }`, solo cuando la respuesta usa el contexto RAG | Sí |
| `tts.started` | `{ text }` | Sí |
| `tts.format` | `{ sample_rate }` | No (send_json directo) |
| `tts.completed` | `{}` | Sí |
| `tts.cancel` | — | No |
| `turn.completed` | `{ text }` | Sí |
| `turn.cancelled` | `{}` | Sí |
| `error` | `{ message }` | Sí |
| `transcript.empty` | — | No (camino no-PCM) |

Binario servidor → cliente: chunks PCM de Piper. **No** van al hub.

Transcript usable: al menos 2 letras (incluye acentos españoles). Si no, se descarta el cierre de utterance.

## `/ws/events`

1. `accept`
2. Primer mensaje `{ "type": "auth", "token" }`
3. Usuario debe tener `organization_id` (ADMIN); si no, 4403
4. `realtime_hub.connect(websocket, organization_id)`
5. Mensajes de texto posteriores se ignoran; binario cierra 1003

Envelope publicado (`backend/app/realtime/events.py`):

```json
{
  "type": "customer.partial",
  "organization_id": "...",
  "conversation_id": "...",
  "payload": { "text": "..." },
  "timestamp": "2026-09-13T00:00:00Z"
}
```

El hub filtra por organización, no por conversación. El frontend de monitoreo puede filtrar por `kognia.auth.conversation-id` en `sessionStorage`.
