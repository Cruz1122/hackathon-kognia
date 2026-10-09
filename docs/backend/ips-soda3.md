# Adapter IPS SODA3 + Redis

El backend integra el dataset `s2ru-bqt6` de datos.gov.co en
`backend/app/ips_soda3/`. Es un adapter dentro del monolito: SODA3 es la
fuente primaria y Redis solo funciona como caché best-effort.

El snapshot que consume el agente se materializa de forma explícita en
PostgreSQL (`ips_snapshots`, `ips_sites`, `ips_capacities`) y en la colección
Chroma independiente `ips_facilities`. FastAPI no descarga el dataset durante
su `lifespan` y ya no carga el corpus Markdown de demostración.

## Configuración

El backend lee el `.env` de la raíz. La plantilla de variables está en
`backend/.env.example`:

- `API_KEY_SODA3` es el token que se envía en `X-App-Token` a SODA3.
- `SECRET_SODA3` contiene el identificador de la aplicación SODA3, pero no se
  envía a datos.gov.co: el contrato SODA3 solo define el token de aplicación.
- `SODA_APP_TOKEN` es un alias compatible con la skill; `API_KEY_SODA3` tiene
  prioridad si ambos están presentes.
- `REDIS_URL` usa `redis://localhost:16379/0` localmente y Compose lo sustituye
  por `redis://redis:6379/0`.

Los TTL predeterminados son 3.600 segundos para respuestas no vacías, 300 para
respuestas vacías y 86.400 para la copia stale. Redis no bloquea la consulta al
origen cuando está caído.

## Rutas

Las rutas requieren el Bearer token de autenticación de la aplicación:

```text
GET /api/ips
GET /api/ips/count
GET /api/ips/capacity
```

Los filtros admitidos son `departamento`, `municipio`, `naturaleza`, `nivel`,
`nombre` y `descripcion_capacidad`; la paginación pública está limitada a 500
filas. El adapter genera las consultas SoQL internamente y nunca acepta SoQL,
columnas u `ORDER BY` desde el cliente.

`/api/ips/count` cuenta filas del dataset, no IPS únicas. `/api/ips/capacity`
agrupa por grupo y descripción de capacidad para no sumar magnitudes
heterogéneas. Las respuestas incluyen `source`, `dataset_id`, `cached`,
`stale` y `fetched_at`.

## Precalentamiento administrativo

No se ejecuta durante una llamada de voz. Con Redis y las variables cargadas:

```bash
cd backend
.venv/bin/python -m app.ips_soda3.prewarm
.venv/bin/python -m app.ips_soda3.prewarm --all-pages
```

La segunda variante pagina como máximo 100 páginas de 1.000 filas y conserva
cada página bajo la política de caché. Un 401/403 del proveedor no se reintenta
ni se reemplaza con datos inventados; los 429 y errores 5xx sí tienen reintentos
acotados.

## Ingesta oficial

Después de aplicar la migración, ejecuta el bootstrap idempotente:

```bash
cd backend
.venv/bin/alembic -c alembic.ini upgrade head
.venv/bin/python -m app.domains.ips.bootstrap
```

La llamada de voz no usa esa ingesta. Las tools consultan SODA3 en vivo con
`API_KEY_SODA3` de `backend/.env`.

Para repetir la ingesta a mano:

```bash
docker compose run --rm -w /app/backend backend \
  python -m app.domains.ips.bootstrap
```

También existe `make ingest-ips` para desarrollo local. El comando pagina todo
el dataset, valida el esquema real, deduplica filas idénticas, consolida por
`c_digo_sede`, calcula un SHA-256 determinista y evita reprocesar el mismo
snapshot. Una nueva versión se crea con estado `staging`; solo se activa después
de que Chroma contenga exactamente un documento por sede. Si PostgreSQL,
embeddings o Chroma fallan, la versión activa anterior permanece sin cambios.

Cada fila de `ips_capacities.raw_record` conserva el registro oficial que la
originó. Solo se eliminan duplicados byte-equivalentes tras canonicalizar JSON;
cantidades distintas para la misma sede/grupo/descripción permanecen como filas
separadas y nunca se suman. Todos los códigos se almacenan como texto. La
cantidad representa capacidad instalada en la fecha de corte y no disponibilidad
en tiempo real.

## Herramientas del agente

El runtime IPS registra cuatro tools de solo lectura:

- `search_ips`: nombre, departamento, municipio y categoría de capacidad.
- `get_ips_details`: dirección y contacto exactos por código de sede.
- `get_ips_capacity`: categorías y cantidades registradas por sede.
- `semantic_search_ips`: recuperación E5/Chroma seguida de hidratación exacta
  desde PostgreSQL.

IPS. La colección histórica `rag_documents` tampoco se consulta desde ese
runtime, incluso si un volumen Chroma antiguo todavía la conserva.
