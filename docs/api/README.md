# API

Contratos HTTP, SSE y WebSocket del proceso FastAPI (`backend/app/main.py`). OpenAPI interactivo: `http://127.0.0.1:18474/docs`.

| Documento | Contenido |
| --- | --- |
| [http.md](http.md) | Rutas REST, bodies, códigos |
| [websockets.md](websockets.md) | `/ws/call` y `/ws/events` |

**CORS** (*verificado*): `FRONTEND_ORIGIN` más `http://localhost:18473` y `http://127.0.0.1:18473`, credentials habilitadas.

**Auth HTTP:** `Authorization: Bearer <access_token>` vía `HTTPBearer`. WebSockets usan el primer mensaje JSON `{ "type": "auth", "token": "..." }`.
