#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
PYTHON="$(python_bin "$ROOT")"

cd "$ROOT"
log_info "Запускаю backend API в локальном режиме"
log_info "Адрес API: ${API_HOST:-127.0.0.1}:${API_PORT:-8000}"
exec "$PYTHON" -m uvicorn apps.api.main:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8000}" --reload
