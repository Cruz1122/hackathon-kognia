# Desarrollo local

**Prerrequisitos:** `python3.13`, Node ≥ 22.12, pnpm, curl. PostgreSQL accesible en `127.0.0.1:15432` (Compose `postgres` o instancia propia).

## Setup

```bash
make setup
```

Efectos (`scripts/setup.sh`):

1. Crea `backend/.venv`
2. Instala `backend/requirements-dev.txt` (runtime + pytest)
3. Descarga Nemotron STT a `backend/models/sherpa-nemotron-35-560/`
4. Descarga Piper a `backend/models/piper-es/`
5. Descarga E5 a `backend/models/multilingual-e5-small/`
6. `pnpm install` en `frontend/`

Los modelos no se versionan. Re-descarga STT/Piper con `scripts/download-sherpa-model.sh` y `scripts/download-piper-model.sh`; el E5 se descarga con `backend/scripts/download-e5-model.py`.

Copiar `backend/.env.example` → `backend/.env` y rellenar `JWT_SECRET_KEY` (≥ 32) y API keys que uses. El backend también lee `.env` en la raíz del repo.

Migrar:

```bash
cd backend && .venv/bin/alembic -c alembic.ini upgrade head
```

Bootstrap SUPERADMIN: ver [Auth](../auth/README.md).

## Procesos

Dos terminales:

```bash
make dev-api   # 127.0.0.1:18474, uvicorn --reload
make dev-web   # 127.0.0.1:18473, astro dev
```

| Servicio | URL |
| --- | --- |
| Web | http://127.0.0.1:18473 |
| API | http://127.0.0.1:18474 |
| OpenAPI | http://127.0.0.1:18474/docs |

**Rollback setup:** borrar `backend/.venv`, `frontend/node_modules` y las carpetas de modelos.

**Side effect:** `--reload` recarga el proceso y pierde el `RealtimeHub` in-memory (suscriptores de monitoreo se caen).
