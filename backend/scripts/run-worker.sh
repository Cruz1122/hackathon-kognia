#!/bin/sh
set -eu

# Azure Container Apps no permite pasar `-m app.worker` como dos argumentos:
# `--args` está declarado nargs='*' y argparse rechaza tokens que empiezan por
# guion, así que un wrapper es la única forma fiable de arrancar el worker.
cd /app/backend

exec python -m app.worker
