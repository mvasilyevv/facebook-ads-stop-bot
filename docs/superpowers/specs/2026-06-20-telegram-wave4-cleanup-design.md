# Дизайн: Telegram — Волна 4 (cleanup + дайджест-спенд money-fix)

Дата: 2026-06-20. Статус: апрувнут. Завершает пересмотр формата TG (волны 1-3). Закрывает
накопленный tech-debt волн 1-2 + один money-баг (дайджест-спенд).

## Контекст
Волны 1-2 (money-надёжность + DM-модель) закрыты и в проде. Накопился tech-debt: мёртвые
fallback-параметры (engine-gate волны 2), мёртвые forum-колонки, dead-code снуза, плюс
не закрытый money-баг занижения спенда в дайджесте (тот же CRIT-1, что чинили в dashboard/history).

## Решение

### A. 🔴 Дайджест-спенд CRIT-1 (money — приоритет)
`core/telegram/digest_builder.py::_top_ads_and_total_spend`: сейчас `DISTINCT ON (ad_id) ORDER BY
cycle_ts DESC` берёт ОДИН последний кумулятивный snapshot в окне `[now-24h]`. Но spend сбрасывается
в cabinet-полночь (≠ UTC midnight), окно 09:00–09:00 UTC пересекает её → теряется спенд предыдущего
дня → **total_spend занижен в 2–5×**. Это тот же CRIT-1, закрытый в `core/dashboard/metric_aggregation.py`.
- Фикс: применить `latest_per_ad_per_day_cte` (DISTINCT ON `(ad_id, date_trunc('day', cycle_ts))`,
  затем SUM по дням) из `metric_aggregation.py`. Переиспользовать, не дублировать.
- Переименовать поле `total_spend_24h_usd` → `total_spend_window_usd`; в `digest_renderer.py`
  заменить «24ч» на реальный диапазон окна.
- Unit-тест: 2 snapshot одного ad до/после полуночи → корректная сумма (не только latest).

### B. DROP мёртвых forum-колонок
Миграция `0024_drop_forum_thread_columns`: `DROP COLUMN forum_warning_thread_id, forum_stop_thread_id,
forum_enable_thread_id, forum_ops_thread_id, forum_digest_thread_id` из `telegram_config`. `chat_id`
ОСТАВИТЬ (безвреден, меньше риск). Делать ПОСЛЕ задачи D (код точно не читает forum). downgrade —
add_column обратно (nullable).

### C. Dead-code снуза
- Удалить `handle_snz_callback` из `core/telegram/handlers/alerts.py` + из `__all__`.
- Убрать stale `snz`-упоминания из docstring'ов (`router.py::_dispatch_callback_query`, `alerts.py` файл-docstring).
- (опц.) явный no-op `elif action == 'snz': answer_callback_query` — тихое закрытие старых кнопок.

### D. Tech-debt fallback-параметров (engine-gate волны 2)
engine всегда задан в проде → fallback-ветки мертвы. Выпилить:
- `core/meta_api/autostop_alert.py::maybe_alert_autostop_channel_down` — убрать параметры
  `tg_client/chat_id/thread` + fallback-ветку (оставить recipients-путь через engine).
- `apps/meta_api_worker/main.py` — `AutostopAlertContext.tg_client/chat_id/thread_id` + `_load_tg`
  (если только для этого) + orphaned `TelegramBotClient`.
- `apps/health_watchdog/main.py::_load_tg` / `_send_alert` fallback-ветка (engine всегда).
- `apps/enable_recommendation_worker/main.py::_default_tg_factory` / `send_alert` fallback.
- Адаптировать тесты волны 1, которые проверяли fallback-путь (`engine=None`) — переписать на
  recipients-only (engine задан) или удалить, если покрытие дублируется.

### E. Мелочи
- Консолидировать `apps/digest_scheduler/main.py::_load_active_recipients` → использовать
  `core.telegram.service.load_active_recipients` (убрать дубль запроса).
- Поправить stale docstring `core/telegram/alert_dispatcher.py::_send_alert_with_fallback`.
- Убрать redundant `recipient and ...` после безусловного гейта в `router.py`.

## Декомпозиция (~5 задач, subagent-driven)
1. **Дайджест-спенд** (money, opus-review) — A.
2. Dead-code снуза + stale docstrings + redundant condition — C + E (docstring/condition части).
3. Tech-debt fallback выпил + адаптация тестов — D.
4. Консолидация digest recipients — E (load_active_recipients часть).
5. Миграция DROP forum-колонок — B (ПОСЛЕДНЕЙ, после D).

## Границы (НЕ в волне 4)
- Mini App + cloudflared — волна 3 (отдельно).
- `chat_id` колонку НЕ дропаем (оставляем).
- sentinel message_id=0 orphan (волна 1 review) — отдельный runbook-вопрос, не код.

## Тестирование
- Unit дайджест-спенд (2 snapshot до/после полуночи; семантика суммы, не latest).
- Полный unit зелёный после каждого выпила (dead-code/fallback не сломали импорты).
- Integration: миграция 0024 применяется/откатывается; дайджест-агрегация на реальных данных.
- Финальный opus broad-review (money-фокус: дайджест-спенд корректен; fallback-выпил не оставил
  prod-дыр; миграция prod-safe).

## Метрика готовности
Дайджест показывает реальный спенд окна (не занижает); мёртвый код/колонки/параметры удалены без
регресса; unit+integration зелёные; ruff чисто; opus-review «Ready to merge».
