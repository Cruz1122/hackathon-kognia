# Docker Compose

**Estado:** verificado contra `compose.yml` (archivo de raíz; no hay `docker-compose.yml`).

```bash
docker compose up --build
```

## Orden

```text
postgres healthy → migrate completed → backend healthy (/health/ready) → frontend
```

| Servicio | Imagen / build | Puerto host | Notas |
| --- | --- | --- | --- |
| `postgres` | `postgres:16.4-alpine` | `127.0.0.1:15432` | volumen `postgres_data` |
| `migrate` | backend Dockerfile | — | one-shot `alembic upgrade head` |
| `backend` | mismo image | `127.0.0.1:18474` | healthcheck ready, `start_period` 60s |
| `frontend` | frontend Dockerfile | `127.0.0.1:18473` → 80 | `PUBLIC_API_URL` en build-arg |

## DATABASE_URL dentro de Compose

`migrate` y `backend` **fuerzan** `postgresql+asyncpg://...@postgres:5432/...`. Un `DATABASE_URL=localhost:15432` del host **no** se interpola en contenedores (comentario en `compose.yml`). En el host, ese URL sí vale para `make dev-api`.

`env_file: backend/.env` es opcional (`required: false`). Sin `JWT_SECRET_KEY` el login falla con 500. Las keys LLM no hacen falta para health/DB.

## Parar

```bash
docker compose down
```

Datos: `docker compose down -v` elimina `postgres_data` (destructivo; solo si quieres resetear la DB).

**Objetivo de plataforma:** mismos servicios más `chroma` y `worker` (misma imagen que backend, otro entrypoint). No son microservicios; aíslan estado vectorial y CPU de jobs. Ver [Arquitectura objetivo](../architecture/target.md).
