#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  echo "Falta backend/.venv. Ejecuta primero ./scripts/setup.sh" >&2
  exit 1
fi

echo "[backend] Compilando módulos Python"
"$ROOT/backend/.venv/bin/python" -m compileall -q "$ROOT/backend/app"

echo "[frontend] Generando build de Astro/Vite"
cd "$ROOT/frontend"
PUBLIC_API_URL="${PUBLIC_API_URL:-http://localhost:18474}" pnpm run build

echo "Build OK"
