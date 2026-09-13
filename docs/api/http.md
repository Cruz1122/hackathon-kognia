# HTTP y SSE

**Estado:** verificado contra `backend/app/main.py` y schemas en `auth/schemas.py`, `features/chat/schemas.py`.

Base local: `http://127.0.0.1:18474`.

## Salud

| Método | Ruta | Auth | 200 | Otros |
| --- | --- | --- | --- | --- |
| GET | `/health/live` | No | `{ status, sherpa, tts, db }` | — |
| GET | `/health/ready` | No | mismos campos, `status: ready` | 503 si Sherpa, Piper o DB no están `ready` |
| GET | `/health` | No | alias de live | deprecated |

`/health/ready` vuelve a comprobar PostgreSQL con timeout de 3 s.

## Utilidad

| Método | Ruta | Auth | Respuesta |
| --- | --- | --- | --- |
| GET | `/api/hello` | No | `{ "message": "FastAPI + Astro funcionando" }` |

## Auth y tenant

| Método | Ruta | Auth | Body | Éxito | Errores |
| --- | --- | --- | --- | --- | --- |
| POST | `/auth/login` | No | `{ email, password }` | `{ access_token, token_type, expires_in, user }` | 401, 500 (JWT no configurado), 503 (DB) |
| GET | `/auth/me` | Bearer | — | `UserResponse` | 401, 503 |
| POST | `/organizations` | SUPERADMIN | `{ name, slug }` | 201 organización | 403, 409, 503 |
| POST | `/organizations/{id}/admins` | SUPERADMIN | `{ email, password }` | 201 usuario ADMIN | 403, 404, 409, 422 (password), 503 |

Login verifica bcrypt incluso si el usuario no existe (`DUMMY_PASSWORD_HASH`) para no filtrar existencia por timing grosero.

## Conversaciones

El tenant sale de `user.organization_id`. SUPERADMIN (`organization_id` null) recibe **403** en estas rutas.

| Método | Ruta | Body | Éxito |
| --- | --- | --- | --- |
| POST | `/conversations` | `{ channel?, status? }` defaults `chat` / `open` | 201 sin mensajes |
| GET | `/conversations/{id}` | — | conversación + mensajes del tenant |

Errores: 401, 403, 404 (fuera de tenant), 409, 503.

## Agente SSE

`POST /ask` — `Accept: text/event-stream`.

Body (`AskRequest`):

- `prompt` 1–10000
- `conversation_id` UUID opcional (camino persistente)
- `messages` lista `{ role, content }` máx. 40 (compatibilidad)
- `channel`, `turn_id` opcionales

Regla: con `conversation_id` se ignora `messages` como fuente de verdad. Sin `conversation_id`, `messages` es obligatorio o 422.

Eventos SSE:

```
event: token
data: {"text":"..."}

event: tool.started
data: {"tool":"...","title":"...","status":"..."}

event: tool.completed
data: {"tool":"...","title":"...","status":"...","result":"..."}

event: done
data: {"provider":"openai","model":"gpt-4o-mini"}

event: error
data: {"message":"..."}
```

Códigos HTTP documentados: 200 stream, 401, 404, 422, 500 (`APP_ENV` inválido), 502 (fallo pre-token), 503 (sin keys o DB).

## Audio HTTP (legacy / utilidades)

| Método | Ruta | In | Out |
| --- | --- | --- | --- |
| POST | `/transcribe` | body binario, `Content-Type` | `{ text }` |
| POST | `/synthesize` | `{ text }` 1–2000 | `audio/wav` |
| POST | `/synthesize/stream` | `{ text }` | PCM `audio/L16` + headers rate/encoding |
| POST | `/voice` | audio + header `X-Chat-History` JSON | PCM L16; **sin auth** en el código actual |

Límites de audio: vacío → 422; > 10 MiB → 413.

`/voice` y `/transcribe` no usan JWT (*verificado* en `main.py`). La demo principal de llamada usa `/ws/call`, que sí autentica.

Headers PCM:

- `X-Audio-Sample-Rate`
- `X-Audio-Encoding: signed-int16-le`
