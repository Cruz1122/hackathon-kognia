# Visión de arquitectura

**Estado:** verificado contra `backend/app/main.py`, `compose.yml` y `frontend/src/pages/`.

## Componentes

| Pieza | Rol | Proceso |
| --- | --- | --- |
| `frontend/` | Demo de llamada (`/`) y monitoreo (`/monitoring`) | Astro/Vite en dev; nginx estático en Docker |
| `backend/app/main.py` | Toda la API HTTP/WS | Un único Uvicorn |
| `backend/app/db/` | Engine asyncpg, modelos, queries tenant-scoped | Mismo proceso |
| `backend/app/providers/` | Contratos LLM/STT/TTS + adapters | Mismo proceso |
| `backend/app/realtime/` | Fan-out JSON por `organization_id` | Memoria del proceso |
| PostgreSQL 16.4 | Organizaciones, usuarios, conversaciones, mensajes | Servicio Compose o host |

## Flujo feliz (demo)

1. SUPERADMIN se crea con `python -m app.auth.bootstrap` (no al startup).
2. SUPERADMIN crea organización y un ADMIN.
3. El ADMIN inicia sesión en `/`; el token y `conversation_id` van a `sessionStorage`.
4. El micrófono envía PCM 16 kHz por `/ws/call` tras `auth` + `conversation.attach`.
5. Sherpa produce parciales; al silencio o fin de utterance el agente responde.
6. Cada chunk semántico se sintetiza con Piper y vuelve como binario por el mismo WS.
7. El turno persiste solo transcript final del usuario y respuesta final del asistente.
8. `/monitoring` se suscribe a `/ws/events` y pinta el mismo tenant (sin PCM).

## Decisiones vigentes

- **Un proceso FastAPI.** El hub no sobrevive a un restart ni escala horizontalmente. *Verificado:* `RealtimeHub` en `backend/app/realtime/hub.py`.
- **Tenant del usuario autenticado.** El cliente no elige `organization_id` en las operaciones de conversación. *Verificado:* `_tenant_id` en `main.py`.
- **Historial autoritativo en PostgreSQL.** `messages` en `/ask` es camino de compatibilidad y no se persiste. *Verificado:* `ask()` en `main.py`.
- **Modelos LLM hardcodeados por `APP_ENV`.** Las keys se leen de env. *Verificado:* `backend/app/config.py`.
- **STT/TTS locales.** Sherpa y Piper se precargan en el lifespan; `/health/live` no los exige, `/health/ready` sí.

## No-objetivos actuales

- Refresh tokens, logout server-side, o sesiones en DB.
- Distribución de audio al monitor.
- Multi-réplica de backend.
- Roles más allá de SUPERADMIN y ADMIN.
- PSTN/Telnyx, Chroma, worker, RAG y Sales Space (visión en [target.md](target.md) y [Producto](../product/README.md)).

## Árbol de código (backend)

```text
backend/app/
  main.py              HTTP + WS + orquestación de llamada
  config.py            APP_ENV y cadena de modelos
  auth/                passwords, JWT, deps, bootstrap
  db/                  session, models, queries
  features/agent/      stream + tools de demo
  features/chat/       schemas Ask/Conversation
  features/transcription/  PCM features + transcribe
  features/synthesis/  schema Piper
  providers/           contratos, LLM, Sherpa, Piper, fakes
  realtime/            envelope + hub
migrations/            Alembic 0001 (vacío) + 0002 (tablas)
```
