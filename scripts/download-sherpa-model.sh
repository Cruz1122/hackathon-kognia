#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/backend/models/sherpa-es"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2"
ARCHIVE="$ROOT/backend/models/sherpa-es.tar.bz2"

if [[ -f "$DEST/tokens.txt" ]]; then
  echo "Modelo Sherpa ya está en $DEST"
  exit 0
fi

mkdir -p "$ROOT/backend/models"
echo "Descargando modelo Sherpa (~119 MB)"
curl -L --fail --show-error -o "$ARCHIVE" "$URL"
rm -rf "$DEST"
mkdir -p "$DEST"
tar -xjf "$ARCHIVE" -C "$ROOT/backend/models"
EXTRACTED="$(find "$ROOT/backend/models" -maxdepth 1 -type d -name 'sherpa-onnx-streaming-zipformer-es-kroko-*' | head -n 1)"
if [[ -n "$EXTRACTED" && "$EXTRACTED" != "$DEST" ]]; then
  mv "$EXTRACTED"/* "$DEST/"
  rmdir "$EXTRACTED"
fi
rm -f "$ARCHIVE"
test -f "$DEST/tokens.txt"
echo "Modelo Sherpa listo en $DEST"
