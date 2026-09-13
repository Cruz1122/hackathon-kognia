# Referencia

Tablas de apoyo. El contrato vivo está en el código enlazado.

| Documento | Contenido |
| --- | --- |
| [scripts.md](scripts.md) | Makefile y `scripts/` |
| [tests.md](tests.md) | Suites pytest / node:test |
| [env.md](env.md) | Lista compacta de env (duplicado operativo) |

## Puertos

| Puerto | Binding | Servicio |
| --- | --- | --- |
| 18473 | `127.0.0.1` | Web |
| 18474 | `127.0.0.1` | API |
| 15432 | `127.0.0.1` | Postgres publicado |

## Dependencias clave

Backend (`requirements.txt`): FastAPI 0.141.1, Uvicorn 0.52.4, SQLAlchemy 2.0.41, asyncpg, Alembic, bcrypt, PyJWT, httpx, sherpa-onnx, piper-tts, pytest.

Frontend: solo `astro@7.3.1`.
