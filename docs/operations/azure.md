# Despliegue en Azure

**Estado:** verificado contra `.github/workflows/deploy-azure.yml`.

Todo corre como contenedores OSS en **Azure Container Apps**. No se usa PaaS
gestionado de Azure (ver [principio AP-15](../product/principles.md)). Las
imágenes se publican en **Docker Hub**, no en ACR.

## Servicios

`compose.yml` lanza 7 servicios; en Azure se reparten así:

| Compose | Azure | Ingress | Notas |
| --- | --- | --- | --- |
| `postgres` | Container App | interno TCP 5432 | `AZURE_DATABASE_APP_NAME` |
| `redis` | Container App | interno TCP 6379 | `AZURE_REDIS_APP_NAME` |
| `chroma` | Container App | interno HTTP 8000 | `AZURE_CHROMA_APP_NAME`, cliente en `:80` |
| `backend` | Container App | externo 18474 | `AZURE_BACKEND_APP_NAME` |
| `worker` | Container App | **ninguno** | `AZURE_WORKER_APP_NAME`, `/app/backend/scripts/run-worker.sh` |
| `frontend` | Container App | externo 80 | `AZURE_FRONTEND_APP_NAME`, nginx estático |
| `migrate` | Container Apps Job | ninguno | `AZURE_BOOTSTRAP_JOB_NAME`, one-shot |

El **worker** comparte la imagen del backend y solo cambia el comando. Como ACA
no admite `working-dir`, el paquete `app` (que vive en `/app/backend/app`) se
resuelve con `PYTHONPATH=/app/backend`. Corre con `min-replicas 1`: hace polling
a Redis y con 0 no consumiría la cola.

El comando es `/bin/sh /app/backend/scripts/run-worker.sh` en lugar de
`python -m app.worker`. No es capricho: `--args` del CLI está declarado
`nargs='*'` y argparse rechaza los tokens que empiezan por guion, así que no hay
forma de pasar `-m` y `app.worker` como dos argumentos separados. El wrapper
(`backend/scripts/run-worker.sh`) hace `exec python -m app.worker`.

## Workflows

| Workflow | Disparador | Alcance |
| --- | --- | --- |
| [deploy-azure.yml](../../.github/workflows/deploy-azure.yml) | `push` a `main` + manual | **Todos** los servicios |
| `deploy-postgres-azure.yml` | manual | PostgreSQL |
| `deploy-redis-azure.yml` | manual | Redis |
| `deploy-chroma-azure.yml` | manual | Chroma |
| `deploy-aplication-azure.yml` | `push` a `main` | backend + frontend (anterior) |
| `setup-demo-db-azure.yml` | manual | migración + admin demo |
| [bootstrap-backend-azure.yml](../../.github/workflows/bootstrap-backend-azure.yml) | `Deploy Azure` completado + manual | migración + admin demo dentro del contenedor del backend |

`deploy-azure.yml` es el punto de entrada: en cada push asegura los siete
servicios y se puede ejecutar a mano con **Run workflow** (despliega todo igual
que el push).

### Orden

```text
detect → infra (postgres + redis + chroma)
       → build-backend → deploy-backend → build-frontend → deploy-frontend → CORS
       → deploy-worker
       → migrate-bootstrap
```

Los builds siguen siendo condicionales (`dorny/paths-filter`): en un push que solo
toca `frontend/**` no se reconstruye la imagen del backend. Un disparo manual
fuerza ambos builds.

### Infraestructura: Redis y Chroma se refrescan, Postgres no

| Servicio | Si no existe | Si ya existe |
| --- | --- | --- |
| PostgreSQL | se crea | **se deja intacto** |
| Redis | se crea | revisión nueva |
| Chroma | se crea | revisión nueva |

Para Redis y Chroma, forzar una revisión nueva es lo que repara una app atascada
en un estado que no es `Running` (imagen inválida, crash-loop, configuración
previa equivocada). Antes de esto había que tirar del workflow manual de ese
servicio.

**PostgreSQL nunca se actualiza**, porque no monta volúmenes en Container Apps:
cualquier revisión nueva arrancaría un contenedor con el filesystem vacío y
**perdería los datos**. Si necesitas que sobrevivan, hay que montar Azure Files o
mover PostgreSQL a un servicio gestionado.

El workflow **no espera ni verifica** que los contenedores queden `Running`: crea,
despliega y lanza el bootstrap. Si algo no arranca, se ve en los logs de Azure
(`az containerapp logs show -n <app> -g <rg>`) o en la pestaña Logs del portal.

El `RealtimeHub` es in-memory, así que el backend se queda en `min-replicas 1`.

### Recuperar la base de datos sin esquema

Como PostgreSQL no tiene volumen, un reinicio del contenedor lo deja con el
clúster vacío: `SELECT 1` de `/health/ready` pasa, pero `/auth/login` responde
`503 (relation "users" does not exist)`. Para eso está
[`bootstrap-backend-azure.yml`](../../.github/workflows/bootstrap-backend-azure.yml):
se dispara solo cuando `Deploy Azure` termina con éxito (y también a mano desde
Actions), espera a que el backend tenga una réplica viva, entra en el contenedor
con `az containerapp exec` y ejecuta `alembic upgrade head` +
`python -m app.auth.bootstrap --demo-only`. Es idempotente y termina comprobando
que `/auth/login` devuelve `200` (o `401` si no hay credenciales demo, que
confirma igualmente que el esquema existe).

## Secrets de GitHub

*Settings → Secrets and variables → Actions → Repository secrets.*

| Secret | Para qué |
| --- | --- |
| `AZURE_CREDENTIALS` | Service principal de `az login` |
| `DOCKERHUB_TOKEN` | Publicar y descargar las imágenes |
| `POSTGRES_PASSWORD` | Password de la Container App de PostgreSQL y del `DATABASE_URL` |
| `JWT_SECRET_KEY` | Firma de access tokens (mínimo 32 caracteres) |
| `OPENAI_API_KEY` | Provider LLM |
| `GEMINI_API_KEY` | Provider LLM |
| `OPENROUTER_API_KEY` | Provider LLM |
| `GROQ_API_KEY` | Provider LLM |
| `TELNYX_API_KEY` | API key de Telnyx (webhooks y llamadas entrantes) |
| `DEMO_ADMIN_PASSWORD` | Password del admin que crea el Job de bootstrap |

Los cuatro providers son opcionales: el workflow solo añade los que tengan
valor, y `APP_ENV=test` funciona con uno solo. Si ninguno está, `/ask` responde
`503` y el resto de la demo sigue viva.

## Variables de GitHub

*Settings → Secrets and variables → Actions → Repository variables.*

| Variable | Default | Para qué |
| --- | --- | --- |
| `AZURE_RESOURCE_GROUP` | — | Resource group |
| `AZURE_LOCATION` | — | Región de las Container Apps |
| `AZURE_CONTAINER_APP_ENVIRONMENT` | — | Entorno de Container Apps compartido |
| `AZURE_DATABASE_APP_NAME` | — | Nombre de la app de PostgreSQL |
| `AZURE_REDIS_APP_NAME` | — | Nombre de la app de Redis |
| `AZURE_CHROMA_APP_NAME` | — | Nombre de la app de Chroma |
| `AZURE_BACKEND_APP_NAME` | — | Nombre de la app del backend |
| `AZURE_WORKER_APP_NAME` | — | Nombre de la app del worker (**obligatoria**: sin ella el job `deploy-worker` falla) |
| `AZURE_FRONTEND_APP_NAME` | — | Nombre de la app del frontend |
| `AZURE_BOOTSTRAP_JOB_NAME` | `hackathon-kognia-bootstrap` | Nombre del Job de migración |
| `DOCKERHUB_USERNAME` | — | Usuario/namespace de las imágenes |
| `POSTGRES_DB` | `kognia` | Base de datos |
| `POSTGRES_USER` | `kognia` | Usuario de PostgreSQL |
| `APP_ENV` | `test` | `test` o `production` (cadena LLM) |
| `CHROMA_RAG_COLLECTION` | `rag_documents` | Colección de RAG |
| `ANALYTICS_CACHE_TTL_SECONDS` | `60` | TTL de la caché en Redis |
| `DEMO_ADMIN_EMAIL` | — | Admin de la demo (opcional) |
| `DEMO_ORG_NAME` | `Demo Kognia` | Organización de la demo |
| `DEMO_ORG_SLUG` | `demo-kognia` | Slug de la organización de la demo |

### Telefonía (Telnyx)

| Variable | Default | Para qué |
| --- | --- | --- |
| `TELNYX_ENABLED` | `false` | Activa el manejo de webhooks de Telnyx |
| `TELNYX_WEBHOOK_HOST` | FQDN del backend | Solo si usas dominio personalizado |
| `TELNYX_CONNECTION_ID` | — | ID de la conexión SIP |
| `TELNYX_PHONE_NUMBER` | — | Número asignado |
| `TELNYX_PUBLIC_KEY` | — | Clave pública para verificar firmas |

**No hace falta configurar `TELNYX_WEBHOOK_HOST`.** El workflow lo deriva del
ingress externo de la Container App del backend, que es a donde Telnyx entrega
los webhooks (`https://<host>/webhooks/telnyx/voice`) y a donde abre el
WebSocket de audio (`wss://<host>/ws/telnyx/stream/...`). En local sí hace falta
ngrok porque el backend no tiene IP pública, y por eso el código trae un host de
ngrok como default: **si se deja sin definir en Azure, Telnyx acaba apuntando al
túnel local y falla en silencio**. Por eso el workflow lo resuelve siempre, y la
variable queda solo como override para un dominio propio.

`TELNYX_ORGANIZATION_ID` y `TELNYX_SYSTEM_USER_ID` **no** se configuran a nivel
de despliegue: el runtime los escribe por tenant.

Solo el **backend** recibe esta configuración. El worker se queda únicamente con
`TELNYX_RECORDINGS_DIR`: `download_recording` consume la URL pre-firmada que
trae la fila de la grabación en PostgreSQL, así que no necesita la API key.

### Reservadas (ningún código las lee todavía)

Declaradas para cuando aterrice el canal, pero hoy el backend no las consume y el
workflow no las inyecta:

| Secret | Variable |
| --- | --- |
| `TYPESAFE_API_KEY` | — |
| `WHATSAPP_ACCESS_TOKEN` | `WHATSAPP_PHONE_NUMBER_ID` |
| `WHATSAPP_APP_SECRET` | `WHATSAPP_WABA_ID` |
| `WHATSAPP_VERIFY_TOKEN` | — |

`whatsapp` ya existe como valor de `Message.channel` y
`Opportunity.recovery_channel` en el esquema, pero no hay integración.

## Primer despliegue

1. Crear el resource group, el Container Apps Environment y registrar las
   variables y secrets de las tablas de arriba.
2. Lanzar **Deploy Azure → Run workflow**. El `workflow_dispatch` fuerza el build
   y deploy del backend y del frontend, y `build-frontend` espera a
   `deploy-backend` para resolver el FQDN del backend.
3. Verificar `https://<FQDN backend>/health/ready` y la web.
