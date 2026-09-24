#!/bin/sh
set -eu

cd /app/backend
exec python -m app.auth.bootstrap --demo-only
