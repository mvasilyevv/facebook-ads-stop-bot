#!/bin/sh
# Точка входа для всех Python-воркеров.
# Выбирает нужный воркер по переменной WORKER_TYPE и делает exec на run_<name>.py.
#
# Имена WORKER_TYPE согласованы с docker-compose.yml (services.<worker>.environment.WORKER_TYPE)
# и с реальными точками входа run_*.py в корне репозитория. Полный набор — 12 воркеров.
# DOM-каналы disable/enable удалены (отключение/включение идёт через meta_api → Marketing API).

set -e

case "${WORKER_TYPE}" in
  observer)
    # Скан Ads Manager + FSM + dispatch алертов. Money: создаёт meta_api_mutation pause_ad на STOP.
    exec python run_observer_worker.py
    ;;
  telegram_poller)
    # Long-polling Telegram Bot API: команды, inline-кнопки под алертами.
    exec python run_telegram_poller.py
    ;;
  cleanup)
    # Раз в сутки: retention, дроп старых партиций, чистка orphan-медиа.
    exec python run_cleanup_worker.py
    ;;
  reconciler)
    # Каждые 30с: stuck task_queue running → retrying, отмена протухших draft.
    exec python run_reconciler_worker.py
    ;;
  meta_api)
    # MONEY-КРИТИЧНО: исполняет meta_api_mutation (авто-стоп pause_ad / включение activate_ad)
    # через Marketing API поверх Vision page.evaluate(fetch).
    exec python run_meta_api_worker.py
    ;;
  health_watchdog)
    # Мониторинг worker:heartbeat:* + network-probe канала Marketing API.
    exec python run_health_watchdog.py
    ;;
  enable_recommendation)
    # Рекомендации на включение восстановившихся объявлений (heartbeat-имя: enable_reco).
    exec python run_enable_recommendation_worker.py
    ;;
  digest_scheduler)
    # Ежедневный TG-дайджест (09:00 UTC, catch-up).
    exec python run_digest_scheduler.py
    ;;
  cabinet_scheduler)
    # MONEY-КРИТИЧНО: автостарт кабинета по расписанию (bulk activate по датам в названии).
    exec python run_cabinet_scheduler.py
    ;;
  tracker_aggregator)
    # Пересчёт tracker_aggregate per (ad, country, day) из adsetpro_postback_events.
    exec python run_tracker_aggregator_worker.py
    ;;
  creator_worker)
    # Исполняет plan_run (создание кампаний через Vision), heartbeat-имя: creator.
    exec python run_creator_worker.py
    ;;
  creator_recorder)
    # Запись планов создания кампаний через CDP (pubsub record_start/record_stop).
    exec python run_creator_recorder.py
    ;;
  migrate)
    # Применение Alembic-миграций до head (one-shot, depends_on у воркеров/api).
    exec python -m alembic upgrade head
    ;;
  *)
    echo "ОШИБКА: неизвестный WORKER_TYPE='${WORKER_TYPE}'"
    echo "Допустимые значения:"
    echo "  observer, telegram_poller, cleanup, reconciler, meta_api, health_watchdog,"
    echo "  enable_recommendation, digest_scheduler, cabinet_scheduler, tracker_aggregator,"
    echo "  creator_worker, creator_recorder, migrate"
    exit 1
    ;;
esac
