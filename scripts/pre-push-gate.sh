#!/usr/bin/env bash
# Гейт перед пушем: не отправлять коммит, который заведомо не сядет, и не
# сажать его поверх уже красной сборки.
#
# Зачем именно перед пушем, а не перед коммитом: коммит локален и дёшев, а
# отправка — это то, после чего чужие коммиты начинают садиться поверх твоего.
# 20.08.2026 сборка была красной пять часов, за это время в main село несколько
# десятков коммитов, и через час уже нельзя было сказать, чьё падение было
# первым.
#
# Две проверки, потому что вопросов тоже два:
#   1. «мой код проходит быстрые тесты» — знает только диск;
#   2. «main сейчас зелёная» — знает только GitHub.
# Локальный хук второе выяснить не может без сети, поэтому спрашивает `gh`.
#
# Обойти осознанно: FB_SKIP_PUSH_GATE=1 git push. Это не «флаг как было», а
# аварийный выход для случая, когда чинишь саму сборку и пуш обязан пройти.

set -euo pipefail

# --- 1. Состояние сборки на main -------------------------------------------
# Красная main означает, что причина падения ещё не найдена. Следующий коммит
# поверх неё смешивает два падения в одно.
#
# `Release` — один workflow и для verify (проверка кода), и для ручного
# деплоя (workflow_dispatch): 24.08.2026 гейт отказал пушу одного тестового
# файла из-за того, что последним прогоном на main оказался ручной деплой,
# упавший на bootstrap ещё не поднятого host — к коду это отношения не имеет.
# Различаем не по имени workflow (оно общее), а по событию: код проверяется
# на push, ручной деплой живёт только в workflow_dispatch. `--event push`
# делает это на уровне запроса к gh, а не пост-фильтрацией.
_gate_check_main_build() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "pre-push: gh не найден — проверка сборки пропущена" >&2
    return 0
  fi

  local run_line
  run_line="$(gh run list --branch main --event push --limit 1 \
    --json conclusion,url,databaseId,workflowName \
    --jq '.[0] | [(.conclusion // ""), (.url // ""), (.databaseId // ""), (.workflowName // "")] | @tsv' \
    2>/dev/null || echo "")"

  if [ -z "$run_line" ]; then
    echo "pre-push: состояние сборки на main неизвестно — пропускаю проверку" >&2
    return 0
  fi

  local conclusion="" run_url="" run_id="" workflow_name=""
  IFS=$'\t' read -r conclusion run_url run_id workflow_name <<<"$run_line"

  case "$conclusion" in
    failure|timed_out|startup_failure)
      local failing_jobs=""
      if [ -n "$run_id" ]; then
        failing_jobs="$(gh run view "$run_id" --json jobs \
          --jq '[.jobs[] | select(.conclusion=="failure") | .name] | join(", ")' \
          2>/dev/null || echo "")"
      fi
      cat >&2 <<MSG
pre-push ОТКАЗ: последний прогон проверок кода (push) на main красный.

Workflow: ${workflow_name:-неизвестен}
Джоба: ${failing_jobs:-неизвестна}
Прогон: ${run_url:-неизвестен}

Коммит поверх красной сборки смешивает своё падение с чужим, и через час уже
не видно, чьё было первым. Сначала разбери красноту:

    gh run view ${run_id:-<id>} --log-failed

Чинишь саму сборку и пуш обязан пройти — FB_SKIP_PUSH_GATE=1 git push
MSG
      return 1
      ;;
    "")
      echo "pre-push: состояние сборки на main неизвестно — пропускаю проверку" >&2
      ;;
  esac
  return 0
}

# --- 2. Быстрые тесты -------------------------------------------------------
# Только unit: полный набор требует PostgreSQL и трёх минут, это работа CI.
# Здесь ловится то, что заведомо не сядет, а не всё подряд.
_gate_run_fast_tests() {
  if [ ! -x .venv/bin/python ]; then
    echo "pre-push: .venv не найден — прогон unit-тестов пропущен" >&2
    return 0
  fi

  echo "pre-push: unit-тесты..." >&2
  if ! PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q --timeout=90; then
    cat >&2 <<'MSG'

pre-push ОТКАЗ: unit-тесты не прошли.

Чинишь причину, а не тест. Обойти осознанно — FB_SKIP_PUSH_GATE=1 git push
MSG
    return 1
  fi
  return 0
}

_gate_main() {
  if [ "${FB_SKIP_PUSH_GATE:-0}" = "1" ]; then
    echo "pre-push: гейт пропущен по FB_SKIP_PUSH_GATE=1" >&2
    return 0
  fi

  local repo_root
  repo_root="$(git rev-parse --show-toplevel)"
  cd "$repo_root"

  _gate_check_main_build
  _gate_run_fast_tests

  echo "pre-push: гейт пройден" >&2
}

# Точка входа только при прямом запуске — сорсинг (в тестах) выполняет
# исключительно функции, без побочных эффектов top-level кода.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  _gate_main "$@"
fi
