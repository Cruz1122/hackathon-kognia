#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:18474}"

echo "Probando $API_URL/health/live"
curl --fail --silent --show-error "$API_URL/health/live"
echo
echo "Probando $API_URL/health/ready"
READY_RESPONSE="$(curl --silent --show-error -w '\n%{http_code}' "$API_URL/health/ready")"
READY_STATUS="${READY_RESPONSE##*$'\n'}"
READY_BODY="${READY_RESPONSE%$'\n'*}"
if [[ "$READY_STATUS" != "200" && "$READY_STATUS" != "503" ]]; then
  echo "El endpoint /health/ready devolvió HTTP $READY_STATUS" >&2
  exit 1
fi
echo "$READY_BODY"
if [[ "${SMOKE_REQUIRE_READY:-0}" == "1" && "$READY_STATUS" != "200" ]]; then
  echo "El backend todavía no está listo para Sherpa/Piper" >&2
  exit 1
fi

if [[ -n "${SMOKE_TOKEN:-}" && -n "${SMOKE_CONVERSATION_ID:-}" ]]; then
  echo "Probando $API_URL/ask (conversación autenticada)"
  ASK_RESPONSE="$(curl --silent --show-error -w '\n%{http_code}' \
    -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    -H "Authorization: Bearer ${SMOKE_TOKEN}" \
    -d "{\"conversation_id\":\"${SMOKE_CONVERSATION_ID}\",\"prompt\":\"ping\"}" \
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
else
  if [[ "${SMOKE_LLM:-0}" == "1" ]]; then
    echo "SMOKE_LLM=1 requiere SMOKE_TOKEN y SMOKE_CONVERSATION_ID" >&2
    exit 1
  fi
  echo "Comprobando que $API_URL/ask exige autenticación"
  ASK_STATUS="$(curl --silent --show-error -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"ping"}' \
    "$API_URL/ask")"
  if [[ "$ASK_STATUS" != "401" ]]; then
    echo "El endpoint /ask no autenticado devolvió HTTP $ASK_STATUS" >&2
    exit 1
  fi
fi

echo "Smoke OK"
