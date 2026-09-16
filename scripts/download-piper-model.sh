#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
VOICE="${PIPER_TTS_VOICE:-es_MX-claude-high}"
CONFIGURED_DIR="${PIPER_MODEL_DIR:-backend/models/piper-es}"

if [[ "$CONFIGURED_DIR" = /* ]]; then
  MODEL_DIR="$CONFIGURED_DIR"
else
  MODEL_DIR="$ROOT/$CONFIGURED_DIR"
fi

mkdir -p "$MODEL_DIR"
if [[ -f "$MODEL_DIR/$VOICE.onnx" && -f "$MODEL_DIR/$VOICE.onnx.json" ]]; then
  echo "Voz Piper $VOICE ya disponible"
  exit 0
fi
"$PYTHON" -m piper.download_voices --download-dir "$MODEL_DIR" "$VOICE"
echo "Voz Piper disponible en $MODEL_DIR"
