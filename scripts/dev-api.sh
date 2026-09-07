#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  echo "Falta backend/.venv. Ejecuta primero make setup" >&2
  exit 1
fi

cd "$ROOT/backend"
exec .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 18474
