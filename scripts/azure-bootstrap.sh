#!/usr/bin/env bash
set -euo pipefail

# Ejecuta Alembic y el bootstrap de admin directamente dentro de una réplica
# activa del backend. Este script es manual y no forma parte del deploy.
# Requiere AZURE_RESOURCE_GROUP, AZURE_BACKEND_APP_NAME, DEMO_ADMIN_EMAIL y
# DEMO_ADMIN_PASSWORD en el entorno. Opcionales: DEMO_ORG_NAME, DEMO_ORG_SLUG,
# AZURE_BACKEND_CONTAINER_NAME (si la app tiene varios contenedores).
# Ejecutar con Azure CLI autenticado: ./scripts/azure-bootstrap.sh

for variable in AZURE_RESOURCE_GROUP AZURE_BACKEND_APP_NAME DEMO_ADMIN_EMAIL DEMO_ADMIN_PASSWORD; do
  if [[ -z "${!variable:-}" ]]; then
    printf 'Falta la variable de entorno %s.\n' "$variable" >&2
    exit 1
  fi
done

command -v az >/dev/null || {
  printf '%s\n' 'Azure CLI (az) no está instalado.' >&2
  exit 1
}
az account show >/dev/null 2>&1 || {
  printf '%s\n' 'Inicia sesión primero con: az login' >&2
  exit 1
}

command -v python3 >/dev/null || {
  printf '%s\n' 'python3 es necesario para escapar de forma segura las variables del comando remoto.' >&2
  exit 1
}

remote_command="$(DEMO_ADMIN_EMAIL="$DEMO_ADMIN_EMAIL" \
  DEMO_ADMIN_PASSWORD="$DEMO_ADMIN_PASSWORD" \
  DEMO_ORG_NAME="${DEMO_ORG_NAME:-Demo Kognia}" \
  DEMO_ORG_SLUG="${DEMO_ORG_SLUG:-demo-kognia}" \
  python3 - <<'PY'
import os
import shlex

environment = {
    "DEMO_ADMIN_EMAIL": os.environ["DEMO_ADMIN_EMAIL"],
    "DEMO_ADMIN_PASSWORD": os.environ["DEMO_ADMIN_PASSWORD"],
    "DEMO_ORG_NAME": os.environ["DEMO_ORG_NAME"],
    "DEMO_ORG_SLUG": os.environ["DEMO_ORG_SLUG"],
}
assignments = " ".join(shlex.quote(f"{key}={value}") for key, value in environment.items())
print(f"env {assignments} /bin/sh /app/backend/scripts/bootstrap-demo-admin.sh")
PY
 )"

args=(
  containerapp debug
  --name "$AZURE_BACKEND_APP_NAME"
  --resource-group "$AZURE_RESOURCE_GROUP"
)
if [[ -n "${AZURE_BACKEND_CONTAINER_NAME:-}" ]]; then
  args+=(--container "$AZURE_BACKEND_CONTAINER_NAME")
fi
args+=(--command "$remote_command")

printf 'Ejecutando migraciones y bootstrap dentro de %s...\n' "$AZURE_BACKEND_APP_NAME"
az "${args[@]}"
