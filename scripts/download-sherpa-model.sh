#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/backend/models/sherpa-nemotron-35-560"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"
ARCHIVE="$ROOT/backend/models/sherpa-nemotron-35-560.tar.bz2"

if [[ -f "$DEST/tokens.txt" && -f "$DEST/encoder.int8.onnx" ]]; then
  echo "Modelo Nemotron ya está en $DEST"
  exit 0
fi

mkdir -p "$ROOT/backend/models"
echo "Descargando Nemotron 3.5 streaming 560 ms (~450 MB)"
curl -L --fail --show-error -o "$ARCHIVE" "$URL"
rm -rf "$DEST"
mkdir -p "$DEST"
tar --no-same-owner -xjf "$ARCHIVE" -C "$ROOT/backend/models"
EXTRACTED="$(find "$ROOT/backend/models" -maxdepth 1 -type d -name 'sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-*' | head -n 1)"
if [[ -n "$EXTRACTED" && "$EXTRACTED" != "$DEST" ]]; then
  mv "$EXTRACTED"/* "$DEST/"
  rmdir "$EXTRACTED"
fi
rm -f "$ARCHIVE"
test -f "$DEST/tokens.txt"
echo "Modelo Nemotron listo en $DEST"
