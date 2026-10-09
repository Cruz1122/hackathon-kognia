# Autenticación y autorización

**Estado:** verificado contra `backend/app/auth/` y `backend/tests/test_auth.py`.

## Piezas

| Módulo | Función |
| --- | --- |
| `passwords.py` | bcrypt, mínimo 8 chars, máximo 72 bytes UTF-8 |
| `tokens.py` | JWT HS256, `sub` = user UUID, `type=access`, `exp` |
| `dependencies.py` | `get_current_user`, `require_superadmin` |
| `bootstrap.py` | crea SUPERADMIN idempotente por email |
| `schemas.py` | email casefold, slug ASCII |

## Token

- Env: `JWT_SECRET_KEY` ≥ 32 caracteres; si no, 500 en login / `AuthConfigurationError`.
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` ∈ [1, 60], default 15.
- `authenticate_token` exige usuario **activo**. Compartido por HTTP y WS.

## Roles

| Rol | `organization_id` | Puede |
| --- | --- | --- |
| SUPERADMIN | NULL | login, `/auth/me`, crear orgs y admins |
| ADMIN | UUID org | login, conversaciones, `/ask`, `/ws/call`, `/ws/events` |

SUPERADMIN **no** puede adjuntar llamadas ni suscribirse a eventos (cierre 4403): no hay tenant.

## Bootstrap

No corre en el lifespan. Una vez por entorno:

```bash
cd backend
SUPERADMIN_EMAIL=admin@example.com \
SUPERADMIN_PASSWORD='cambia-esta-clave' \
  .venv/bin/python -m app.auth.bootstrap
```

Docker:

```bash
docker compose exec -w /app/backend \
  -e SUPERADMIN_EMAIL=admin@example.com \
  -e SUPERADMIN_PASSWORD='cambia-esta-clave' \
  backend python -m app.auth.bootstrap
```

Si el email ya existe como SUPERADMIN sin org, sale 0 (“already exists”). Si existe con otro rol, falla.

## Frontend

`index.astro` guarda:

- `sessionStorage['kognia.auth.access-token']`
- `sessionStorage['kognia.auth.conversation-id']`

Tras login crea una conversación y arranca `/ws/call`. `/monitoring` usa el `conversation-id` para filtrar envelopes del hub.
