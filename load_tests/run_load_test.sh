#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

compose=(
  docker compose
  --env-file "${SCRIPT_DIR}/load.env"
  -f "${PROJECT_DIR}/docker-compose.yml"
  -f "${PROJECT_DIR}/docker-compose.load.yml"
)

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans
}
trap cleanup EXIT

cd "${PROJECT_DIR}"
"${compose[@]}" config --quiet
"${compose[@]}" build load-test
"${compose[@]}" up -d --build db redis mock-llm migrate api worker
"${compose[@]}" run --rm --no-deps load-test
