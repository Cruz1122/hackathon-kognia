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

El setup crea `backend/.venv`, instala las dependencias de `backend/requirements.txt` y ejecuta `pnpm install` dentro de `frontend/`.

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
| `GET` | `/health` | `{"status":"ok"}` |
| `GET` | `/api/hello` | Mensaje de conexión entre la web y la API |
| `POST` | `/ask` | Stream SSE de tokens generado por el provider configurado |

La web usa `/api/hello` al cargar y muestra un mensaje de error si el backend no está disponible.

## Configuración

El backend carga automáticamente el archivo `.env` ubicado en la raíz del repositorio. Usa `.env.example` como plantilla:

- `FRONTEND_ORIGIN`: origen permitido por CORS en la API. Por defecto, `http://localhost:18473`.
- `PUBLIC_API_URL`: URL base que usa la web para llamar a la API. Por defecto, `http://localhost:18474`.
- `APP_ENV`: entorno LLM, `test` o `production`. Por defecto, `test`.
- `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`: credenciales de providers; no se versionan.

Los modelos están hardcodeados en `backend/app/config.py`. En `test` el fallback circular es Gemini → MiniMax vía OpenRouter → Llama vía Groq, con tres intentos por modelo y nueve intentos globales. En `production` el orden es GPT-5.6 Luna vía OpenAI → Gemini, con tres intentos por modelo.

`POST /ask` recibe `{"prompt":"..."}` y responde con `text/event-stream`, emitiendo eventos `token`, `done` o `error`. Los códigos HTTP documentados son `200` (stream iniciado), `422` (prompt inválido), `500` (APP_ENV inválido), `502` (fallo de providers antes del primer token) y `503` (sin API keys). Un fallo después del primer token no puede cambiar el código HTTP porque la respuesta ya empezó; en ese caso se emite un evento SSE `error` sin reiniciar la respuesta.

Puedes exportar las variables antes de iniciar cada proceso, por ejemplo:

```bash
PUBLIC_API_URL=http://localhost:18474 make dev-web
FRONTEND_ORIGIN=http://localhost:18473 make dev-api
```

## Build y validación

```bash
make build
```

Este comando compila los módulos Python y genera el build de Astro/Vite. Para ejecutar el health check con la API levantada:

```bash
make smoke
```

El smoke test consulta `http://127.0.0.1:18474/health`. Puedes cambiar la URL con `API_URL`:

```bash
API_URL=http://127.0.0.1:18474 make smoke
```

El smoke también comprueba que `/ask` responde. Para exigir una respuesta LLM completada con credenciales reales, ejecuta `SMOKE_LLM=1 API_URL=http://127.0.0.1:18474 make smoke`.

## Estructura

```text
backend/
  app/main.py       API FastAPI, CORS y endpoints iniciales
  requirements.txt  Dependencias Python
frontend/
  src/pages/index.astro  Página inicial y llamada al backend
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

Antes de ampliar el proyecto, revisa `AGENTS.md` y `.agents/skills/hackathon/SKILL.md`.
