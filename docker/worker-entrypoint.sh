#!/bin/sh
# Точка входа для всех Python-воркеров
# Выбирает нужный воркер по переменной WORKER_TYPE

set -e

case "${WORKER_TYPE}" in
  observer)
    exec python run_observer.py
    ;;
  disable)
    exec python run_disable_worker.py
    ;;
  enable)
    exec python run_enable_worker.py
    ;;
  enable_recommendation)
    exec python run_enable_recommendation_worker.py
    ;;
  telegram_poller)
    exec python -m apps.telegram_poller.main
    ;;
  health_watchdog)
    exec python -m apps.health_watchdog.main
    ;;
  migrate)
    exec python -m alembic upgrade head
    ;;
  *)
    echo "ОШИБКА: неизвестный WORKER_TYPE='${WORKER_TYPE}'"
    echo "Допустимые значения: observer, disable, enable, enable_recommendation, telegram_poller, health_watchdog, migrate"
    exit 1
    ;;
esac
