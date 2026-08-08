#!/bin/sh
# Точка входа для всех Python-воркеров.
# Выбирает нужный воркер по переменной WORKER_TYPE и делает exec на run_<name>.py.
#
# Имена WORKER_TYPE согласованы с root local и deploy/compose service contracts.
# Полный production-набор — 13 воркеров плюс one-shot migrator.
# DOM-каналы disable/enable удалены (отключение/включение идёт через meta_api → Marketing API).

set -e

case "${WORKER_TYPE}" in
  observer)
    # Скан Ads Manager + FSM + dispatch алертов. Money: создаёт meta_api_mutation pause_ad на STOP.
    exec python run_observer_worker.py
    ;;
  telegram_delivery)
    # Единственный HTML Bot API gateway; читает durable notification outbox.
    exec python -m apps.telegram_delivery_worker.main
    ;;
  telegram_updates)
    # Обрабатывает durable webhook inbox; long polling отсутствует.
    exec python -m apps.telegram_update_worker.main
    ;;
  cleanup)
    # Раз в сутки: retention, дроп старых партиций, чистка orphan-медиа.
    exec python run_cleanup_worker.py
    ;;
  reconciler)
    # Каждые 30с: stuck task_queue running → retrying, отмена протухших draft.
    exec python run_reconciler_worker.py
    ;;
  autopause)
    # MONEY-КРИТИЧНО: единственный consumer lane=money. Обычный meta_api worker
    # fail-closed и не имеет доступа к этой lane.
    exec python run_autopause_worker.py
    ;;
  meta_api)
    # Ручные/interactive/bulk/background mutations. lane=money запрещена контрактом.
    exec python run_meta_api_worker.py
    ;;
  health_watchdog)
    # Durable stuck-task/snapshot checks + live network probe Marketing API.
    exec python run_health_watchdog.py
    ;;
  enable_recommendation)
    # Рекомендации на включение восстановившихся объявлений.
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
  tracker_reconciliation_worker)
    # Durable postback processing + provider reconciliation.
    exec python run_tracker_reconciliation_worker.py
    ;;
  campaign_creator)
    # Исполняет campaign_create (залив FB-кампаний из UI: uniquify→upload→create
    # через Marketing API). Money-критично: кампании PAUSED, partial-create без retry.
    exec python run_campaign_creator_worker.py
    ;;
  migrate)
    # Единственный normal migration path: advisory lock, fresh-target preflight,
    # upgrade и check выполняются одной locked-обёрткой.
    exec python -m scripts.run-migrations-locked
    ;;
  *)
    echo "ОШИБКА: неизвестный WORKER_TYPE='${WORKER_TYPE}'"
    echo "Допустимые значения:"
    echo "  observer, telegram_delivery, telegram_updates, cleanup, reconciler, autopause, meta_api, health_watchdog,"
    echo "  enable_recommendation, digest_scheduler, cabinet_scheduler, tracker_reconciliation_worker,"
    echo "  campaign_creator, migrate"
    exit 1
    ;;
esac
