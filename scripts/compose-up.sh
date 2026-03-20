#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
COMPOSE="$(compose_cmd)"

cd "$ROOT"
log_info "Запускаю compose-стек"
eval "$COMPOSE up -d postgres redis migrator api worker browser_host"
