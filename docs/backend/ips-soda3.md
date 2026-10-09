# Adapter IPS SODA3 + Redis

El backend integra el dataset `s2ru-bqt6` de datos.gov.co en
`backend/app/ips_soda3/`. Es un adapter dentro del monolito: SODA3 es la
fuente primaria y Redis solo funciona como caché best-effort.

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
