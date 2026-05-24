#!/bin/bash
# Прогон unit-тестов по логическим блокам. Каждый блок имеет жёсткий лимит
# по времени (BLOCK_TIMEOUT) — если не уложился, блок убивается и помечается
# как timeout, остальные блоки продолжаются.
#
# Использование:
#   bin/test-blocks.sh                 # все блоки
#   bin/test-blocks.sh rules observer  # выборочно
set -o pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTEST=".venv/bin/pytest"
BLOCK_TIMEOUT="${BLOCK_TIMEOUT:-60}"  # лимит на блок, сек
PYTEST_TIMEOUT="${PYTEST_TIMEOUT:-5}" # лимит на тест, сек

# Имена блоков
ALL_BLOCKS="rules observer telegram alerts api workers ai infra misc"

# Файлы по блокам (имя_блока → список файлов).
files_for_block() {
  case "$1" in
    rules)
      echo "tests/unit/test_evaluator.py tests/unit/test_bayesian_smoothing.py \
            tests/unit/test_frequency_anomaly.py tests/unit/test_rule_confidence.py \
            tests/unit/test_time_weights.py tests/unit/test_adaptive_cpa.py"
      ;;
    observer)
      echo "tests/unit/test_observer_scenarios.py tests/unit/test_observer_heartbeat_loop.py \
            tests/unit/test_observer_self_healing.py"
      ;;
    telegram)
      echo "tests/unit/test_telegram_poller.py tests/unit/test_telegram_poller_register_ui.py \
            tests/unit/test_telegram_bot_handler.py tests/unit/test_telegram_renderer.py \
            tests/unit/test_digest.py"
      ;;
    alerts)
      echo "tests/unit/test_alerts_queue.py"
      ;;
    api)
      echo "tests/unit/test_api_dashboard.py tests/unit/test_top_ads_status.py \
            tests/unit/test_api_enable_recommendations.py tests/unit/test_health_router.py \
            tests/unit/test_health_watchdog.py tests/unit/test_health_watchdog_scanning_gate.py"
      ;;
    workers)
      echo "tests/unit/test_base_task_worker.py tests/unit/test_disable_worker_heartbeat.py"
      ;;
    ai)
      echo "tests/unit/test_explain_alert.py tests/unit/test_ai_cache.py"
      ;;
    infra)
      echo "tests/unit/test_logging_setup.py tests/unit/test_metrics.py \
            tests/unit/test_last_scan_at.py tests/unit/test_pubsub.py \
            tests/unit/test_ws_endpoint.py tests/unit/test_supervisor_crashmail.py"
      ;;
    misc)
      # Всё остальное — динамически.
      enumerated="$(
        for b in rules observer telegram alerts api workers ai infra; do
          files_for_block "$b"
        done | tr -s ' \n' '\n' | sort -u
      )"
      ls tests/unit/test_*.py 2>/dev/null | sort -u | \
        comm -23 - <(echo "$enumerated") | tr '\n' ' '
      ;;
  esac
}

# Какие блоки запускать.
if [ $# -gt 0 ]; then
  REQUESTED="$*"
else
  REQUESTED="$ALL_BLOCKS"
fi

PASS_BLOCKS=""
FAIL_BLOCKS=""
TIMEOUT_BLOCKS=""
EMPTY_BLOCKS=""

mkdir -p .logs

for block in $REQUESTED; do
  files="$(files_for_block "$block")"
  existing=""
  for f in $files; do
    [ -f "$f" ] && existing="$existing $f"
  done

  if [ -z "${existing// }" ]; then
    echo "═══ [$block] пусто — пропуск ═══"
    EMPTY_BLOCKS="$EMPTY_BLOCKS $block"
    continue
  fi

  echo ""
  echo "═══ [$block] лимит ${BLOCK_TIMEOUT}s ═══"

  $PYTEST $existing -q --timeout="$PYTEST_TIMEOUT" --timeout-method=thread \
    > ".logs/test-block-$block.log" 2>&1 &
  pid=$!

  elapsed=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1
    elapsed=$((elapsed + 1))
    if [ "$elapsed" -ge "$BLOCK_TIMEOUT" ]; then
      echo "⏱  таймаут блока — убиваю pid $pid"
      pkill -9 -P "$pid" 2>/dev/null
      kill -9 "$pid" 2>/dev/null
      TIMEOUT_BLOCKS="$TIMEOUT_BLOCKS $block"
      break
    fi
  done

  wait "$pid" 2>/dev/null
  rc=$?

  tail -3 ".logs/test-block-$block.log" | sed 's/^/    /'

  if [ "$rc" -eq 0 ]; then
    PASS_BLOCKS="$PASS_BLOCKS $block"
  elif echo " $TIMEOUT_BLOCKS " | grep -q " $block "; then
    :
  else
    FAIL_BLOCKS="$FAIL_BLOCKS $block"
  fi
done

echo ""
echo "═══ ИТОГ ═══"
echo "✅ pass:    ${PASS_BLOCKS:-—}"
echo "❌ fail:    ${FAIL_BLOCKS:-—}"
echo "⏱  timeout: ${TIMEOUT_BLOCKS:-—}"
echo "·  empty:   ${EMPTY_BLOCKS:-—}"
echo ""
echo "Логи: .logs/test-block-*.log"
