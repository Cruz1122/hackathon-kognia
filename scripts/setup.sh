#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3.13 >/dev/null 2>&1; then
  echo "ERROR: se requiere Python 3.13" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: se requiere Node.js >= 22.12" >&2
  exit 1
fi

if ! command -v pnpm >/dev/null 2>&1; then
  echo "ERROR: se requiere pnpm" >&2
  exit 1
fi

PY_MINOR="$(python3.13 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PY_MINOR" != "3.13" ]]; then
  echo "ERROR: python3.13 no apunta a Python 3.13" >&2
  exit 1
fi

node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 12)) process.exit(1)' || {
  echo "ERROR: se requiere Node.js >= 22.12" >&2
  exit 1
}

echo "[1/5] Creando entorno virtual backend/.venv con Python 3.13"
python3.13 -m venv "$ROOT/backend/.venv"

echo "[2/5] Instalando dependencias Python dentro del entorno virtual"
"$ROOT/backend/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/backend/.venv/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"

echo "[3/5] Descargando modelo Sherpa-ONNX español"
"$ROOT/scripts/download-sherpa-model.sh"

echo "[4/5] Descargando voz Piper Claude en español mexicano"
"$ROOT/scripts/download-piper-model.sh"

echo "[5/5] Instalando dependencias del frontend"
cd "$ROOT/frontend"
pnpm install

echo
echo "Setup completo. Ejecuta: make dev-api y make dev-web"
