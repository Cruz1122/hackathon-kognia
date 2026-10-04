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

## STT / TTS

| Variable | Default |
| --- | --- |
| `SHERPA_MODEL_DIR` | `backend/models/sherpa-nemotron-35-560` |
| `SHERPA_THREADS` | `2` en Compose |
| `PIPER_MODEL_DIR` | `backend/models/piper-es` |
| `PIPER_TTS_VOICE` | `es_MX-claude-high` |

## Postgres Compose

`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — default `kognia`.

## Smoke

| Variable | Efecto |
| --- | --- |
| `API_URL` | base del smoke |
| `SMOKE_REQUIRE_READY=1` | exige `/health/ready` 200 |
| `SMOKE_LLM=1` | exige SSE `event: done` |
| `SMOKE_TOKEN` + `SMOKE_CONVERSATION_ID` | ejercita `/ask` persistente |
