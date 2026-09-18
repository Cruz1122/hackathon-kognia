#!/usr/bin/env bash
set -euo pipefail

MAX_USED_SPACE="${DOCKER_BUILDX_CACHE_MAX_SPACE:-5GB}"

echo "Pruning Docker BuildKit cache (keeping at most ${MAX_USED_SPACE})"
docker buildx prune --all --force --max-used-space "$MAX_USED_SPACE"
