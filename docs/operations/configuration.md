# Configuración

Plantilla: `backend/.env.example`. No versionar `.env` ni keys.

## Red y app

| Variable | Default | Uso |
| --- | --- | --- |
| `FRONTEND_ORIGIN` | `http://localhost:18473` | CORS |
| `PUBLIC_API_URL` | `http://localhost:18474` | URL que embebe el frontend |
| `APP_ENV` | `test` | `test` o `production` (cadena LLM) |
| `DATABASE_URL` | local `localhost:15432` | dialecto `postgresql+asyncpg` |

## Auth

| Variable | Default | Uso |
| --- | --- | --- |
| `JWT_SECRET_KEY` | vacío | obligatorio ≥ 32 para login |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 15 | 1–60 |
| `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` | vacío | solo bootstrap CLI |

## LLM (lazy)

`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`

## Precios (modo dev)

| Variable | Default | Uso |
| --- | --- | --- |
| `PRICING_CACHE_TTL_SECONDS` | `21600` (6 h) | TTL de la caché Redis del catálogo |
| `PRICING_SOURCE_URL` | `https://openrouter.ai/api/v1/models` | catálogo de precios público |

## IPS SODA3 + Redis

| Variable | Default | Uso |
| --- | --- | --- |
| `API_KEY_SODA3` | vacío | token de aplicación enviado como `X-App-Token` |
| `SECRET_SODA3` | vacío | identificador de la aplicación; no se envía al proveedor |
| `SODA_APP_TOKEN` | vacío | alias compatible con la skill cuando no existe `API_KEY_SODA3` |
| `IPS_CACHE_TTL_SECONDS` | `3600` | TTL de respuestas con filas |
| `IPS_CACHE_EMPTY_TTL_SECONDS` | `300` | TTL de respuestas vacías |
| `IPS_CACHE_STALE_TTL_SECONDS` | `86400` | TTL de fallback stale ante errores transitorios |
| `IPS_HTTP_TIMEOUT_SECONDS` | `10` | timeout por solicitud a SODA3 |
| `IPS_HTTP_MAX_ATTEMPTS` | `3` | máximo de intentos para 429/5xx/transporte |
| `IPS_CHROMA_COLLECTION` | `ips_facilities` | colección semántica independiente de sedes |
| `IPS_EMBEDDING_BATCH_SIZE` | `128` | sedes procesadas por lote de embeddings/upsert |

El dataset se consulta por `POST` a la URL fija de SODA3 para `s2ru-bqt6`; no
se expone el token al frontend.

## STT / TTS

| Variable | Default |
| --- | --- |
| `SHERPA_MODEL_DIR` | `backend/models/sherpa-nemotron-35-560` |
| `SHERPA_THREADS` | `2` en Compose |
| `PIPER_MODEL_DIR` | `backend/models/piper-es` |
| `PIPER_TTS_VOICE` | `es_MX-claude-high` |

## Postgres Compose

`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — default `kognia`.

## Telephony (Telnyx)

Leídas por `backend/app/telephony/settings.py`. En Azure se configuran como
variables/secrets de GitHub; ver [azure.md](azure.md).

| Variable | Default | Uso |
| --- | --- | --- |
| `TELNYX_ENABLED` | `false` | habilita el manejo de webhooks |
| `TELNYX_WEBHOOK_HOST` | host de ngrok | host público de los webhooks; en Azure lo deriva el workflow |
| `TELNYX_API_KEY` | vacío | API key (descarga de grabaciones) |
| `TELNYX_PUBLIC_KEY` | vacío | verifica firmas de webhook |
| `TELNYX_CONNECTION_ID` | vacío | conexión SIP |
| `TELNYX_PHONE_NUMBER` | vacío | número asignado |
| `TELNYX_RECORDINGS_DIR` | `/var/lib/kognia/recordings` | grabaciones en disco |
| `TELNYX_CAPTURE_DIR` | `/tmp/telnyx-captures` | capturas |

`TELNYX_ORGANIZATION_ID` y `TELNYX_SYSTEM_USER_ID` los escribe el runtime por
tenant; no se configuran globalmente.

## Smoke

| Variable | Efecto |
| --- | --- |
| `API_URL` | base del smoke |
| `SMOKE_REQUIRE_READY=1` | exige `/health/ready` 200 |
| `SMOKE_LLM=1` | exige SSE `event: done` |
| `SMOKE_TOKEN` + `SMOKE_CONVERSATION_ID` | ejercita `/ask` persistente |
