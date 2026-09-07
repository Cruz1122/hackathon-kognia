#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:18474}"

echo "Probando $API_URL/health"
curl --fail --silent --show-error "$API_URL/health"
echo
echo "Probando $API_URL/ask (configuración local)"
ASK_RESPONSE="$(curl --silent --show-error -w '\n%{http_code}' \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"prompt":"ping"}' \
  "$API_URL/ask")"
ASK_STATUS="${ASK_RESPONSE##*$'\n'}"
ASK_BODY="${ASK_RESPONSE%$'\n'*}"

if [[ "$ASK_STATUS" != "200" && "$ASK_STATUS" != "502" && "$ASK_STATUS" != "503" ]]; then
  echo "El endpoint /ask devolvió HTTP $ASK_STATUS" >&2
  exit 1
fi

if [[ "${SMOKE_LLM:-0}" == "1" && ( "$ASK_STATUS" != "200" || "$ASK_BODY" != *"event: done"* ) ]]; then
  echo "El endpoint /ask no completó el stream" >&2
  exit 1
fi

echo "Smoke OK"
