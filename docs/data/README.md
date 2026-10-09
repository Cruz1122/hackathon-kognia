# Datos

PostgreSQL es la fuente de verdad de organizaciones, usuarios y el historial de conversación.

| Documento | Contenido |
| --- | --- |
| [schema.md](schema.md) | Tablas, constraints, queries |

**Runtime:** SQLAlchemy 2 async + asyncpg. URL dialecto `postgresql+asyncpg://`.

**Migraciones:** Alembic en `backend/alembic.ini` y `backend/migrations/`.

| Revisión | Qué hace |
| --- | --- |
| `0001_initial` | Vacía; fija el head inicial |
| `0002_persistent_conversations` | Crea `organizations`, `users`, `conversations`, `messages` |

Comando Compose: `alembic -c backend/alembic.ini upgrade head` (servicio `migrate`).

Local, con venv y DB arriba:

```bash
cd backend
.venv/bin/alembic -c alembic.ini upgrade head
```

**Default local:** `postgresql+asyncpg://kognia:kognia@localhost:15432/kognia` (`backend/app/db/session.py`).

**Producto:** Postgres es la verdad transaccional (AP-08). Entidades futuras (calls, recordings, jobs, sales) se añaden **con el reto**, no un CRM anticipado. `Conversation` debe poder agrupar canales; no igualar call ≡ conversation (AP-06). Ver [Arquitectura objetivo](../architecture/target.md).
