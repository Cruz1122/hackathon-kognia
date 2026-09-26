#!/bin/sh
set -eu

cd /app/backend

echo "Applying database migrations..."
alembic -c alembic.ini upgrade head

if [ -n "${DEMO_ADMIN_EMAIL:-}" ] && [ -n "${DEMO_ADMIN_PASSWORD:-}" ]; then
  echo "Creating demo admin..."
  exec python -m app.auth.bootstrap --demo-only
fi

echo "DEMO_ADMIN_* not configured; migrations only."
