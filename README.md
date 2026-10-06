<h1 align="center">Proyecto Hackaton</h1>

<p align="center">
  <img src="./assets/kognia-hacka-badge.svg" alt="Kognia Hacka" />
  <img src="https://img.shields.io/badge/Python-3.13-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/FastAPI-0.141.1-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI 0.141.1" />
  <img src="https://img.shields.io/badge/Astro-7.3.1-BC52EE?style=flat&logo=astro&logoColor=white" alt="Astro 7.3.1" />
</p>

<p align="center"><em>Un starter FastAPI + Astro para construir una demo rápido.</em></p>

## Sobre el proyecto

Kognia Hacka es un starter pequeño para construir una demo con una API FastAPI y una web Astro. La pantalla inicial consulta al backend y muestra su respuesta, así que el camino feliz está listo para extenderse.

## Stack

- Python 3.13
- FastAPI 0.141.1 + Uvicorn 0.52.4
- Astro 7.3.1 + Vite
- Node.js >= 22.12
- pnpm 11.3.0

## Requisitos

`make setup` comprueba que estén disponibles `python3.13`, Node.js y pnpm. También necesitas `curl` para ejecutar el smoke test.

## Instalación y desarrollo

Desde la raíz del repositorio:

```bash
make setup
```

El setup crea `backend/.venv`, instala las dependencias runtime y de desarrollo de `backend/requirements-dev.txt`, descarga los modelos locales de voz y RAG, y ejecuta `pnpm install` dentro de `frontend/`.

Levanta la API y la web en terminales separadas:

```bash
make dev-api
make dev-web
```

También están disponibles los scripts equivalentes `scripts/dev-api.sh` y `scripts/dev-web.sh`.

| Servicio | URL |
| --- | --- |
| Web | http://127.0.0.1:18473 |
| API | http://127.0.0.1:18474 |
| Documentación OpenAPI | http://127.0.0.1:18474/docs |

## API disponible

| Método | Ruta | Respuesta |
| --- | --- | --- |
| `GET` | `/health/live` | Estado del proceso de la API; no requiere Sherpa/Piper ni providers |
| `GET` | `/health/ready` | `200` cuando Sherpa-ONNX, Piper y PostgreSQL están listos; `503` mientras no lo estén |
| `GET` | `/health` | Alias legacy de `/health/live` |
| `GET` | `/api/hello` | Mensaje de conexión entre la web y la API |
| `POST` | `/auth/login` | Access token JWT y datos públicos del usuario |
| `GET` | `/auth/me` | Datos públicos del usuario autenticado |
| `POST` | `/organizations` | Crea una organización; requiere SUPERADMIN |
| `POST` | `/organizations/{id}/admins` | Crea un ADMIN en la organización; requiere SUPERADMIN |
| `POST` | `/conversations` | Crea una conversación del tenant autenticado |
| `GET` | `/conversations/{id}` | Recupera una conversación solo dentro del tenant autenticado |
| `POST` | `/ask` | Stream SSE de tokens generado por el provider configurado |
| `POST` | `/voice` | Recibe audio y devuelve únicamente audio PCM generado por el agente |
| `POST` | `/transcribe` | Transcripción local de audio con Sherpa-ONNX |
| `POST` | `/synthesize` | Audio WAV local con Piper TTS |
| `POST` | `/synthesize/stream` | Audio PCM de Piper TTS por chunks |
| `WS` | `/ws/call` | Sesión autenticada: auth + attach, PCM del mic, parciales Sherpa, agente, tools y TTS |
| `WS` | `/ws/events` | Suscripción autenticada a eventos JSON del tenant |

La web incluye una demo de llamada por voz en `index.astro`: captura PCM 16 kHz y lo envía por `/ws/call`. El backend lo transcribe en streaming con Sherpa-ONNX (Nemotron 3.5 español, 560 ms), consume el stream del agente y pasa cada chunk semántico a Piper TTS con la voz mexicana `es_MX-claude-high`. El modelo se carga una sola vez y usa una configuración determinista para no omitir ni variar palabras entre generaciones. El backend serializa la generación con un único worker.

`make setup` descarga el modelo STT a `backend/models/sherpa-nemotron-35-560/` (ignorado por git). También puedes correr `scripts/download-sherpa-model.sh`.

`make setup` descarga la voz Piper Claude junto al modelo Sherpa. Al arrancar el backend, el `lifespan` carga Sherpa-ONNX y la voz Piper sin exigir API keys. `/health/live` indica que el proceso responde y `/health/ready` devuelve `200` solo cuando Sherpa-ONNX, Piper y PostgreSQL están listos (o `503` si alguna capacidad no está disponible). La página `/monitoring` se suscribe a `/ws/events` cuando hay un access token en `sessionStorage` (el mismo de la demo de llamada). Sin sesión sigue mostrando la línea de tiempo local de ejemplo. El hub es in-memory y no transporta PCM.

Las conversaciones autenticadas son la fuente de verdad en PostgreSQL: cada turno persiste únicamente el transcript final del usuario y la respuesta final del asistente. El request legacy puede seguir enviando `messages` sin `conversation_id` durante la transición; esa lista no se persiste y no sustituye el historial de una conversación adjunta.

```json
{
  "prompt": "¿Y después?",
  "messages": [
    {"role": "user", "content": "Hola"},
    {"role": "assistant", "content": "Hola, ¿cómo estás?"}
  ],
  "channel": "voice-demo"
}
```

## Configuración

El backend carga automáticamente el archivo `.env` ubicado en la raíz del repositorio. Usa `.env.example` como plantilla:

- `FRONTEND_ORIGIN`: origen permitido por CORS en la API. Por defecto, `http://localhost:18473`.
- `PUBLIC_API_URL`: URL base que usa la web para llamar a la API. Por defecto, `http://localhost:18474`.
- `APP_ENV`: entorno LLM, `test` o `production`. Por defecto, `test`.
- `JWT_SECRET_KEY`: secreto privado de al menos 32 caracteres para firmar los access tokens; no se versiona.
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`: duración del access token, entre 1 y 60 minutos. Por defecto, `15`.
- `SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`: credenciales usadas únicamente por el bootstrap explícito inicial; no se crean usuarios automáticamente al arrancar.
- `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`: credenciales de providers; no se versionan.
- `PIPER_MODEL_DIR`: carpeta que contiene la voz Piper. Por defecto, `backend/models/piper-es`.
- `PIPER_TTS_VOICE`: nombre de la voz local. Por defecto, `es_MX-claude-high`.

Los modelos están hardcodeados en `backend/app/config.py`. En `test` se prioriza `gpt-5.4-mini` vía OpenAI por su baja latencia y soporte de tool calling y streaming por tokens; el fallback circular continúa con Gemini → MiniMax vía OpenRouter → Llama vía Groq, con tres intentos por modelo y doce intentos globales. En `production` el orden es GPT-6 Luna vía OpenAI → Gemini, con tres intentos por modelo.

`POST /ask` recibe `{"conversation_id":"...","prompt":"..."}` y responde con `text/event-stream`. Cuando la respuesta del LLM es útil y coincide con el contexto recuperado, emite `rag.started`/`rag.completed` con `{ "used_rag": true, "message": "..." }`; `message` es el tópico normalizado del documento o de la sección recuperada. Las respuestas de aclaración o los mensajes incompletos no emiten RAG. Si no se usa retrieval no se emite ningún evento RAG. El servidor carga el historial de esa conversación por tenant, persiste el mensaje de usuario antes de llamar al agente y persiste la respuesta final antes de emitir `done`. Los códigos HTTP documentados son `200` (stream iniciado), `401` (auth ausente/inválida), `404` (conversación fuera del tenant o inexistente), `422` (prompt inválido), `500` (APP_ENV inválido), `502` (fallo de providers antes del primer token) y `503` (sin API keys o DB no disponible). Un fallo después del primer token no puede cambiar el código HTTP porque la respuesta ya empezó; en ese caso se emite un evento SSE `error` sin reiniciar la respuesta.

`/ws/call` exige como primeros mensajes, en ese orden, `{"type":"auth","token":"..."}` y `{"type":"conversation.attach","conversation_id":"..."}`. El servidor cierra con `4401` si el token no es válido y no acepta comandos ni audio antes de autenticar y adjuntar la conversación. Los mensajes binarios se mantienen dentro de `/ws/call`; no forman parte de ningún canal de eventos JSON.

`/ws/events` usa el mismo primer mensaje `auth` y entrega envelopes JSON con `type`, `organization_id`, `conversation_id`, `payload` y `timestamp`. El `RealtimeHub` es in-memory y vive dentro del único proceso FastAPI; filtra por organización y elimina subscribers cuyo socket ya no puede recibir. No distribuye PCM, niveles de audio ni chunks binarios de TTS.

El login devuelve un access token JWT. En las rutas protegidas se envía como `Authorization: Bearer <token>`. El usuario SUPERADMIN tiene `organization_id = NULL` y es el único que puede crear organizaciones y sus primeros admins. El ADMIN siempre pertenece a una organización; la autorización de futuras operaciones debe derivar el tenant del usuario autenticado, no de un `organization_id` enviado por el cliente.

El SUPERADMIN inicial se crea mediante un comando explícito e idempotente, una sola vez por entorno:

```bash
cd backend
SUPERADMIN_EMAIL=admin@example.com \
SUPERADMIN_PASSWORD='cambia-esta-clave' \
  .venv/bin/python -m app.auth.bootstrap
```

Dentro de Docker el módulo vive en `/app/backend`:

```bash
docker compose exec -w /app/backend \
  -e SUPERADMIN_EMAIL=admin@example.com \
  -e SUPERADMIN_PASSWORD='cambia-esta-clave' \
  backend python -m app.auth.bootstrap
```

El comando requiere `DATABASE_URL` si no se usa el valor local predeterminado y no se ejecuta automáticamente durante el startup del backend.

Puedes exportar las variables antes de iniciar cada proceso, por ejemplo:

```bash
PUBLIC_API_URL=http://localhost:18474 make dev-web
FRONTEND_ORIGIN=http://localhost:18473 make dev-api
```

Para Docker, `docker compose up --build` arranca PostgreSQL, ejecuta `alembic upgrade head` en el servicio one-shot `migrate`, y después inicia el backend y el frontend. El orden depende de health/completion: PostgreSQL healthy → migración completada → backend healthy → frontend. PostgreSQL usa la imagen explícita `postgres:16.4-alpine`, el volumen nombrado `postgres_data` y publica solo en `127.0.0.1:15432`; la API y la web conservan `127.0.0.1:18474` y `127.0.0.1:18473`.

En Compose, `DATABASE_URL` de `migrate` y `backend` apunta siempre a `postgres:5432` usando `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`. Un `DATABASE_URL=localhost:15432` del `.env` del host (válido para `make dev-api`) no se interpola dentro de los contenedores.

La conexión se configura con `DATABASE_URL` usando el dialecto `postgresql+asyncpg`. En Docker el valor predeterminado apunta al servicio `postgres`; para desarrollo local con `make dev-api`, `backend/.env.example` apunta al puerto publicado `15432`. Las credenciales locales predeterminadas son `kognia`/`kognia` y pueden reemplazarse junto con `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` y `DATABASE_URL`. Alembic vive en `backend/alembic.ini` y `backend/migrations/`; la revisión `0001` prepara la base y `0002` crea las tablas mínimas de organizaciones, usuarios, conversaciones y mensajes.

`backend/.env` es opcional para levantar la infraestructura sin providers, pero Docker necesita que coloques allí `JWT_SECRET_KEY` para usar login y `SUPERADMIN_*` para ejecutar el bootstrap. Las credenciales de providers permanecen fuera del repositorio, se cargan de forma lazy y no son necesarias para comprobar PostgreSQL o los endpoints de health.

## Build y validación

```bash
make build
```

Este comando compila los módulos Python y genera el build de Astro/Vite. Para ejecutar el health check con la API levantada:

```bash
make smoke
```

El smoke test consulta `http://127.0.0.1:18474/health/live` y comprueba también `/health/ready` (incluyendo el estado `db`). Puedes cambiar la URL con `API_URL`:

```bash
API_URL=http://127.0.0.1:18474 make smoke
```

Para exigir que los modelos locales estén listos durante el smoke test, añade `SMOKE_REQUIRE_READY=1`.

El smoke también comprueba que `/ask` responde. Para exigir una respuesta LLM completada con credenciales reales, ejecuta `SMOKE_LLM=1 API_URL=http://127.0.0.1:18474 make smoke`.

Con auth configurada, pasa un token y una conversación ya creada para ejercitar el request persistente:

```bash
SMOKE_TOKEN='<access-token>' \
SMOKE_CONVERSATION_ID='<conversation-uuid>' \
  API_URL=http://127.0.0.1:18474 make smoke
```

## Estructura

```text
backend/
  app/main.py       API FastAPI, CORS y endpoints
  app/auth/         Passwords, JWT, dependencias y bootstrap explícito
  app/db/           Engine, sesión, modelos y helpers tenant-scoped
  app/providers/    Contratos LLM/STT/TTS, adapters y fakes
  app/realtime/     Envelope de eventos y hub JSON in-memory
  migrations/       Revisiones Alembic
  requirements.txt  Dependencias Python
frontend/
  src/pages/index.astro  Pantalla de llamada
  src/features/voice-call/  STT, streaming, chunking, TTS y cancelación
  package.json           Scripts y dependencias Astro
assets/
  kognia-logo.svg        Logo del proyecto
  kognia-hacka-badge.svg Badge con el logo y nombre del proyecto
scripts/
  setup.sh          Instalación local
  build.sh          Compilación completa
  smoke.sh          Health check de la API
Makefile            Atajos de desarrollo y validación
```

La documentación completa está en [`docs/`](docs/README.md): código actual, operación, y la estrategia de plataforma (principios, pivot, telefonía e intelligence).

Antes de ampliar el proyecto, revisa `AGENTS.md` y `.agents/skills/hackathon/SKILL.md`.
