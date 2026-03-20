#!/usr/bin/env bash

set -euo pipefail

repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$script_dir/.." >/dev/null 2>&1
  pwd
}

log_info() {
  printf '[ИНФО] %s\n' "$*"
}

log_warn() {
  printf '[ПРЕДУПРЕЖДЕНИЕ] %s\n' "$*"
}

log_error() {
  printf '[ОШИБКА] %s\n' "$*" >&2
}

die() {
  log_error "$*"
  exit 1
}

ensure_command() {
  command -v "$1" >/dev/null 2>&1 || die "Команда '$1' не найдена"
}

python_bin() {
  if [[ -x "$1/.venv/bin/python" ]]; then
    printf '%s\n' "$1/.venv/bin/python"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  die "Не найден Python 3"
}

compose_cmd() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    printf '%s\n' "docker compose"
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' "docker-compose"
    return
  fi

  die 'Не найдено ни `docker compose`, ни `docker-compose`'
}

has_compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    return 0
  fi

  return 1
}
