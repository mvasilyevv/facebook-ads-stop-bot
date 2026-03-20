#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
COMPOSE="$(compose_cmd)"

cd "$ROOT"
log_info "Останавливаю compose-стек"
eval "$COMPOSE down --remove-orphans"
