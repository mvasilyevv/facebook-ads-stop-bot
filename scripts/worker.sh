#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
PYTHON="$(python_bin "$ROOT")"

cd "$ROOT"
log_info "Запускаю фонового воркера в локальном режиме"
exec "$PYTHON" -m apps.worker.main
