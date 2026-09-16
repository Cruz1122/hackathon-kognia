#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIMIT_BYTES="${COMPOSE_IMAGE_SIZE_LIMIT_BYTES:-3500000000}"

mapfile -t images < <(docker compose -f "$ROOT/compose.yml" config --images | sort -u)
if ((${#images[@]} == 0)); then
  echo "No Compose images found" >&2
  exit 1
fi

declare -A seen_ids=()
total=0
for image in "${images[@]}"; do
  read -r image_id image_size < <(docker image inspect --format '{{.Id}} {{.Size}}' "$image")
  if [[ -n "${seen_ids[$image_id]:-}" ]]; then
    echo "$image: shared image ($image_id), counted once"
    continue
  fi
  seen_ids["$image_id"]="$image"
  total=$((total + image_size))
  printf '%-36s %12d bytes\n' "$image" "$image_size"
done

printf 'Unique image total: %d bytes (' "$total"
awk -v bytes="$total" 'BEGIN { printf "%.2f GB)\n", bytes / 1000000000 }'
printf 'Configured limit:    %d bytes (' "$LIMIT_BYTES"
awk -v bytes="$LIMIT_BYTES" 'BEGIN { printf "%.2f GB)\n", bytes / 1000000000 }'

if ((total >= LIMIT_BYTES)); then
  echo "Compose image total exceeds the configured limit" >&2
  exit 1
fi

echo "Compose image size OK"
