# Дизайн: Telegram — Волна 2 (DM-модель, убрать супергруппу)

Дата: 2026-06-19. Статус: апрувнут пользователем. Продолжение волны 1 (см.
`2026-06-19-telegram-wave1-money-reliability-design.md`). Часть пересмотра формата TG.

## Контекст и цель

Целевой формат TG (решение владельца): **доставка в личку (DM)**, супергруппа/форум-топики
убираются. Волна 1 ввела `notify_owners` (DM owner'ам) для НОВЫХ money-нотификаций воркеров.
Волна 2 — перевести ВЕСЬ канал на DM-recipients и снести супергруппу/топики. Это автоматически
закрывает класс group-ACL багов из аудита (callback-group-broken, group-auth-bypass, topics-acl).

**Адресаты (решение владельца): всем активным recipients** (owner + наблюдатели-байеры),
рассылка каждому в личку.

Текущее состояние: `alert_dispatcher` шлёт ОДНО сообщение в `config.chat_id` (супергруппа),
дедуп по `UNIQUE(chat_id, ad_id, incident_key, stream_kind)`. `forum_*_thread_id` маршрутизирует
по топикам в ~10 местах. Онбординг (`/start`) жёстко создаёт `role="recipient"` — owner не
появляется штатно (обойдено вручную в волне 1).

## Решение

### A. Рассылка всем recipients (сердце, money-критично)
- Новая `load_active_recipients(engine) -> list[Recipient]` (owner + recipient, `revoked_at IS NULL`)
  в `core/telegram/service.py`, рядом с `load_owner_recipients`.
- **`alert_dispatcher.dispatch_pending_alerts` / `sweep_orphan_alerts`**: вместо одного
  `config.chat_id` — **внешний цикл по recipients**, каждому свой pre-claim+send в личку
  (`chat_id=recipient.chat_id`). Дедуп per-chat работает из коробки: `UNIQUE` уже включает
  `chat_id` → N recipients = N строк `telegram_message_refs`. `retry-sweep` адаптируется:
  осиротевший = событие без `message_ref` **для конкретного recipient'а** (NOT EXISTS с
  `r.chat_id = :rid`); цикл по recipients × sweep.
- Если recipients пусто — пропускаем (как сейчас при `chat_id IS NULL`), лог.
- Кнопки `dis/ereco/dr_ok` под каждым алертом в личке работают: `find_recipient(chat_id=private,
  user_id)` находит recipient (group-callback баг исчезает — нет группы).

### B. Унификация worker-нотификаций на recipients
- `core/telegram/worker_notify.py`: добавить `notify_recipients(engine, redis, *, category, text,
  dedup_key, dedup_ttl_seconds) -> bool` (все активные recipients) рядом с `notify_owners`.
  Решение владельца «всем» → money/ops-нотификации воркеров идут всем recipients.
- Перевести с `chat_id`+`thread_id` на recipients: `AutostopAlertContext` (meta_api channel-down),
  `health_watchdog`, `reconciler` (`_maybe_alert_irreversible`), `enable_reco`, `digest_scheduler`.
  Каждый из них сейчас грузит `cfg.chat_id` + `forum_ops/digest_thread_id` — заменить на
  `notify_recipients`/`notify_owners` (digest — всем; health/reconciler/channel-down — всем).

### C. Убрать супергруппу/форум-топики
- Перестать использовать `forum_*_thread_id` (везде `message_thread_id=None` / убрать параметр из
  путей доставки). **Колонки `forum_*_thread_id`/`chat_id` в `telegram_config` оставить мёртвыми**
  (DROP — отдельной миграцией в волне 4 cleanup, чтобы не плодить риск сейчас).
- Удалить: `core/telegram/topics.py` (`PgTopicStore`), `core/telegram/handlers/topics.py`
  (`/setup_topics`, `/topics`), их регистрацию в `router`, topics-логику в
  `apps/api/routers/v1/settings_telegram.py` и `settings_compute`. Убрать `message_thread_id` из
  `handlers/*` (bulk/creator/spy/autostart/_send), `messaging.py`, `core/alerts/send.py`.

### D. Системный owner-invite + group-ACL cleanup
- **Миграция (Alembic)**: добавить колонку `role VARCHAR(16) NOT NULL DEFAULT 'recipient'` в
  `telegram_invites`. `create_telegram_invite.py` пишет реальную роль; `find_active_invite`
  возвращает `role`; `onboarding` передаёт `invite["role"]` в `consume_invite_and_create_recipient`
  (убрать hardcoded `role="recipient"`). Owner-invite → реальный owner.
- group-ACL: бот работает только в личке. В `router` убрать `_is_private`-гейт-дыру — `if not
  recipient: «нет доступа»; return` для ВСЕХ команд кроме `/start`, независимо от типа чата.
  Это снимает group-auth-bypass / topics-group-acl (group-callback уже не актуален без группы).

## Границы (НЕ в волне 2)
- DROP мёртвых колонок `forum_*_thread_id`/`chat_id` — волна 4 (cleanup).
- Mini App + cloudflared (web_app-кнопки под алертами) — волна 3.
- dead-code снуза, дайджест per-day CTE spend — волна 4.
- UI scan-controls (прервать идущий скан) — отдельный roadmap-трек (`docs/roadmap/ui-scan-controls.md`).

## Тестирование
- Unit `load_active_recipients` (owner+recipient, revoked исключены).
- Unit `notify_recipients` (рассылка всем, dedup-after-send, no-recipients → False).
- Integration `alert_dispatcher`: 2 recipients → 2 message_refs (per-chat дедуп), повторный
  dispatch не задваивает; sweep per-recipient ресендит только своему сироте.
- Unit/integration onboarding: owner-invite → role='owner' recipient.
- Регресс: убедиться, что удаление топиков не сломало пути доставки (thread_id=None ок).

## Метрика готовности
Все алерты/нотификации идут всем активным recipients в личку; супергруппа/топики не используются;
owner-invite штатно создаёт owner; group-ACL дыры закрыты; unit+integration зелёные; ruff чисто;
opus-review «Ready to merge». Живой прогон: STOP-алерт приходит всем recipients в личку с рабочими
кнопками.
