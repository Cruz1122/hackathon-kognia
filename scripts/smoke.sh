#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:18474}"

echo "Probando $API_URL/health"
curl --fail --silent --show-error "$API_URL/health"
echo
echo "Smoke OK"
