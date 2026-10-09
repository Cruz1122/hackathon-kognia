#!/bin/sh
set -eu

cd /app/backend

echo "Applying database migrations..."

# The PostgreSQL Container App may still be completing startup when this job
# begins. A TCP ingress can accept the connection before PostgreSQL is ready
# to complete the protocol handshake, so retry the whole Alembic invocation.
# PostgreSQL DDL is transactional here, so `upgrade head` is safe to repeat
# after a transient connection failure.
migration_attempt=1
while [ "$migration_attempt" -le 30 ]; do
  if alembic -c alembic.ini upgrade head; then
    break
  fi

  if [ "$migration_attempt" -eq 30 ]; then
    echo "Database migrations failed after $migration_attempt attempts." >&2
    exit 1
  fi

  echo "Database is not ready; retrying migrations (attempt $((migration_attempt + 1))/30)..." >&2
  migration_attempt=$((migration_attempt + 1))
  sleep 5
done

if [ -n "${DEMO_ADMIN_EMAIL:-}" ] && [ -n "${DEMO_ADMIN_PASSWORD:-}" ]; then
  echo "Creating demo admin..."
  exec python -m app.auth.bootstrap --demo-only
fi

echo "DEMO_ADMIN_* not configured; migrations only."
