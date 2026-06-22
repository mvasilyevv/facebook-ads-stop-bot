# Архитектура: Telegram-подсистема

Дата анализа: 2026-06-22. Read-only аудит, источник — исходный код.

---

## Назначение

Telegram-подсистема выполняет три независимые роли:

1. **Доставка алертов** — отправка HTML-карточек в личку каждому recipient'у при обнаружении warning/stop observer'ом.
2. **Приём команд** — long-polling loop, парсинг slash-команд и inline-кнопок, делегирование доменным handlers.
3. **Нотификации воркеров** — best-effort DM-уведомления владельцам о money-событиях (провал авто-стопа, critical channel down и т.д.).

---

## Компоненты

| Файл | Роль |
|------|------|
| `client.py` / `TelegramBotClient` | Минимальный async HTTP-клиент Telegram Bot API: send/edit/poll/forum. HTML-обрезка и автобаланс тегов. Retry 429 (1 раз, cap 30s) + 502/503/504 (2 попытки с backoff). |
| `service.py` | Доступ к БД: `load_telegram_config` (расшифровка токена), `load_active_recipients`, `load_owner_recipients`, `find_recipient`, `find_active_invite`, `consume_invite_and_create_recipient`, `save_poller_offset`, `touch_poller_heartbeat`. |
| `alert_dispatcher.py` | Deliver + retry: `dispatch_pending_alerts` (по scan_id) и `sweep_orphan_alerts` (24h-окно осиротевших). Pre-claim через `INSERT ... ON CONFLICT DO NOTHING RETURNING id` с sentinel `message_id=0` для атомарного дедупа. |
| `renderer.py` | Pure-рендер alert-карточки (`AlertRenderInput` → текст HTML + inline-клавиатура). Зависит только от `format.py`. |
| `format.py` | Pure-форматтеры: `esc/b/i/code/quote`, `money/num/pct/unit/multiplier`, `kv_grid/table/bullets`. Единый источник для всех рендереров (алерты, дайджест, enable-reco, health-нотификации). |
| `messaging.py` / `safe_edit_or_send_message` | Fallback edit→send с набором игнорируемых ошибок (not_modified, thread deleted и т.д.). |
| `worker_notify.py` | `notify_owners` / `notify_recipients` — best-effort DM с dedup через Redis SET NX (только после успешной доставки). Кеш TelegramBotClient по токену. |
| `settings_compute.py` | Вычисляемые поля для API: `compute_is_authorized`, `compute_poller_status`, `compute_bot_username` (Redis TTL 1h + getMe). |
| `web_app_url.py` | CRUD `web_app_url` в `system_config`. `normalize_web_app_base` валидирует https-prefix. |
| `digest_builder.py` | Pure-агрегации для ежедневного дайджеста (5 SQL-запросов, все с partition pruning). Использует `latest_per_ad_per_day_cte` из `core.dashboard`. |
| `digest_renderer.py` | HTML-рендер DigestPayload → TG-сообщение. |
| `bot_handler.py` | Тонкий фасад: реэкспортирует `handle_update` из `handlers/` (обратная совместимость). |
| `handlers/router.py` | Центральный диспетчер update → домен-handler. ACL-гейт (recipient check + owner-only check). |
| `handlers/onboarding.py` | `/start [code]` (invite consume), `/help`. |
| `handlers/alerts.py` | Обработка `dis:` (pause_ad через Marketing API + mark_alert_state_claimed best-effort) и `ereco:` (activate_ad). |
| `handlers/draft_confirm.py` | `dr_ok` / `dr_cancel` под /pause /resume превью. Owner ACL через `approve_draft_task` + `is_admin_recipient` fallback. |
| `handlers/bulk.py` | `/pause <offer>` / `/resume <offer>` — owner-scoped резолв ad_ids + создание DRAFT bulk_status_change. |
| `handlers/autostart.py` | `/autostart [on/off/HH:MM]` — чтение/запись конфига cabinet_scheduler. |
| `handlers/spy.py` | `/spy <slot> <country>` — Ad Library pipeline в asyncio background task. |
| `handlers/creator.py` | `/record_plan`, `/stop_record`, `/plans`, callback `plan:<uuid>` — creator workflow через Redis pubsub + task_queue. |
| `handlers/_send.py` | Thin helper `send_text`: глушит сетевые ошибки, parse_mode=HTML default. |
| `apps/telegram_poller/main.py` | Long-polling loop с hot-reload токена, heartbeat (Redis + БД), graceful shutdown. |

---

## Последовательности вызовов

### А. Dispatch алерта (observer → Telegram)

```
observer_worker (process_scan_rows)
  └─ alert_dispatcher.dispatch_pending_alerts(engine, client, scan_id, redis)
       1. load_telegram_config(engine)               → проверка bot_token
       2. load_active_recipients(engine)             → list[Recipient]
       3. load_web_app_url(engine)                   → web_app_base (once)
       4. engine.connect() → SELECT alert_events     ← partition pruning: created_at >= NOW()-1h
            JOIN fb_ads, fb_adsets, fb_campaigns, offers
            WHERE scan_id = :sid
       5. Для каждого event × recipient:
            _deliver_one_alert(engine, client, ...)
              a. engine.begin() → INSERT telegram_message_refs
                   ON CONFLICT DO NOTHING RETURNING id    ← atomic pre-claim
                   (sentinel message_id=0)
              b. Если RETURNING NULL → skip (дублирование уже заклеймировано)
              c. render_alert_text(AlertRenderInput)      ← pure, format.py
              d. render_inline_keyboard(AlertRenderInput) ← pure
              e. client.send_message(...)                 ← HTTP POST Telegram API
              f. Если send failed → DELETE claim_row (освобождаем для retry)
              g. Если succeed → UPDATE message_refs SET message_id=real_id
              h. _publish_alert_created(redis, ...)       ← best-effort
```

### Б. Sweep осиротевших алертов (конец каждого scan-цикла)

```
observer_worker
  └─ alert_dispatcher.sweep_orphan_alerts(engine, client, redis, hours=24)
       1. load_telegram_config, load_active_recipients
       2. Для каждого recipient r:
            SELECT alert_events за [NOW()-24h, NOW()]
              WHERE open_state_token IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM telegram_message_refs
                WHERE ... AND chat_id = r.chat_id        ← per-chat dedup
              )
            Для каждого orphan → _deliver_one_alert(...)  (те же шаги А.5.a-h)
```

### В. Long-polling (Telegram → handlers)

```
apps/telegram_poller/main_loop()
  ├─ heartbeat_loop (asyncio.Task) → Redis SET worker:heartbeat:telegram_poller TTL 60s
  ├─ touch_poller_heartbeat(engine) → UPDATE telegram_config SET poller_heartbeat_at
  ├─ load_telegram_config(engine) → re-check токена каждые 60s (hot-reload)
  └─ client.get_updates(offset) → long-poll 25s
       └─ handle_update(engine, client, update, redis)
            ├─ callback_query → _dispatch_callback_query(engine, client, cq)
            │    1. find_recipient(engine, chat_id, user_id)    ← ACL check
            │    2. action ∈ _OWNER_ONLY_CALLBACKS → owner check
            │    3. dispatch → handle_dis_callback / handle_enable_reco_callback
            │                  / handle_draft_callback / handle_plan_run_callback
            └─ message (slash-command)
                 1. cmd == 'start' → handle_start (без auth)
                 2. find_recipient (auth gate)
                 3. needs_owner check
                 4. dispatch → handle_help / handle_spy / handle_bulk_toggle
                              / handle_autostart / handle_record_plan / handle_stop_record
                              / handle_list_plans
```

### Г. Worker notify (воркеры → owner DM)

```
meta_api_worker / health_watchdog / cabinet_scheduler
  └─ worker_notify.notify_owners(engine, redis, category=..., text=..., dedup_key=..., dedup_ttl=...)
       1. Redis GET dedup_key → если есть, skip
       2. load_telegram_config(engine) → bot_token
       3. load_owner_recipients(engine) → list[Recipient role='owner']
       4. _client_for_token(token) → кеш TelegramBotClient (по токену)
       5. Для каждого owner: client.send_message(chat_id, text)
       6. Если delivered → Redis SET NX dedup_key
```

### Д. dis: callback (ручное отключение из TG-карточки)

```
Telegram → long-poll update (callback_query, data="dis:<fb_ad_id>:<token_short>")
  → _dispatch_callback_query
    → find_recipient (ACL: только owner)
    → handle_dis_callback(engine, client, cq_id, fb_ad_id, token, username)
         1. load_ad_account_id_for_fb_ad(engine, fb_ad_id)
         2. create_mutation_task(engine, mutation_kind='pause_ad', ...)
              → INSERT task_queue status='pending'
         3. mark_alert_state_claimed(engine, fb_ad_id) — best-effort
         4. client.answer_callback_query(cq_id, ack)
```

---

## Зависимости

### Что зависит от telegram-подсистемы

- `apps/observer_worker` — вызывает `dispatch_pending_alerts` и `sweep_orphan_alerts` напрямую.
- `apps/digest_scheduler` — использует `digest_builder.build_digest` + `digest_renderer` + `client.py` напрямую.
- `apps/health_watchdog` — вызывает `worker_notify.notify_owners/notify_recipients`.
- `apps/meta_api_worker`, `apps/cabinet_scheduler`, `apps/enable_recommendation_worker` — аналогично.
- `apps/api/routers/v1/settings_telegram.py` — использует `service.py`, `settings_compute.py`, `client.py`.

### От чего зависит telegram-подсистема

- `core.crypto.decrypt` — расшифровка bot_token.
- `core.pubsub.CHANNEL_ALERT_CREATED` — publish в Redis после доставки.
- `core.meta_api.queue` — создание mutation task в `handle_dis_callback`, `handle_enable_reco_callback`.
- `core.meta_api.schemas.MetaMutationPayload`.
- `core.observer.writers.mark_alert_state_claimed` — best-effort в `handle_dis_callback`.
- `core.observer.queries.load_observer_config` — owner_campaign_tag для bulk.py.
- `core.meta_api.bulk.resolve_owner_ad_ids` — резолв ad_ids по офферу.
- `core.tasks.queue.create_task` — создание plan_run task.
- `core.ad_library.pipeline.run_pipeline` — Ad Library pipeline для /spy.
- `core.scheduler.cabinet_autostart.read_autostart_config / write_autostart_config`.
- `core.dashboard.metric_aggregation.latest_per_ad_per_day_cte` — в digest_builder.
- PostgreSQL таблицы: `telegram_config`, `telegram_recipients`, `telegram_invites`, `telegram_message_refs`, `alert_events`, `fb_ads`, `fb_adsets`, `fb_campaigns`, `offers`, `task_queue`, `creator_plans`, `system_config`.
- Redis: `worker:heartbeat:telegram_poller`, `fb_agent:alert:created` (publish), произвольные dedup-ключи из вызывающих воркеров, `tg:bot_username` (TTL 1h), `ai:ratelimit:*`.

---

## Потоки данных

### Доставка алерта

```
alert_events (partitioned, cycle: scan_id, created_at)
  ↓ SELECT + JOIN (fb_ads, fb_adsets, fb_campaigns, offers)
AlertRenderInput (frozen dataclass, pure)
  ↓ render_alert_text() → HTML-строка
  ↓ render_inline_keyboard() → TG reply_markup dict
client.send_message() → Telegram API (HTTPS)
  ↓ message_id
telegram_message_refs (INSERT sentinel → UPDATE real_id)
  ↓ Redis publish (CHANNEL_ALERT_CREATED, best-effort)
```

### Inline-кнопка dis:

```
TG callback_query (data="dis:<fb_ad_id>:<token8>")
  ↓ find_recipient (telegram_recipients) → Recipient
  ↓ load_ad_account_id_for_fb_ad (fb_ads) → ad_account_id
  ↓ create_mutation_task → task_queue (status='pending')
  ↓ mark_alert_state_claimed → ad_alert_state (best-effort)
  ↓ answer_callback_query → Telegram API
```

### Recipient onboarding

```
/start <code> (message)
  ↓ find_active_invite (telegram_invites) → invite row
  ↓ consume_invite_and_create_recipient (транзакция):
      UPDATE telegram_invites SET used_at=NOW()
      INSERT telegram_recipients ON CONFLICT DO UPDATE
  ↓ send_message → Telegram API
```

---

## Внешние взаимодействия

| Внешняя система | Способ | Направление | Что |
|----------------|--------|-------------|-----|
| Telegram Bot API | httpx HTTPS | out | sendMessage, getUpdates, answerCallbackQuery, editMessage, createForumTopic, setMyCommands |
| PostgreSQL | asyncpg (SQLAlchemy) | in/out | telegram_config/recipients/invites/message_refs, alert_events, task_queue, creator_plans и др. |
| Redis | redis.asyncio | out | heartbeat SET (TTL 60s), publish fb_agent:alert:created, SET NX dedup-ключи, GET tg:bot_username кэш |
| gRPC browser-agent | через creator.py → Redis pubsub | indirect | /record_plan → fb_agent:creator:record_start; /stop_record → record_stop |
| Meta Marketing API | через task_queue outbox | indirect | dis: / ereco: создают meta_api_mutation task → meta_api_worker исполняет |

---

## Инварианты и контракты

1. **Pre-claim дедуп**: UNIQUE(chat_id, ad_id, incident_key, stream_kind) в `telegram_message_refs` гарантирует, что даже при N параллельных dispatch'ах одно TG-сообщение будет отправлено ровно один раз для каждой (recipient, инцидент) пары.

2. **incident_key**: при наличии `open_state_token` используется `str(open_token)`, иначе `event-{event_id}`. Первый вариант корректен для дедупа инцидентов; второй — fallback для событий без токена (дедупирует только в пределах одного event_id, что безопасно).

3. **Dedup sweep**: `sweep_orphan_alerts` фильтрует `AND r.chat_id = :cid` в NOT EXISTS, чтобы sweep не считал «уже отправлено» при наличии ref для другого recipient'а.

4. **Partition pruning в dispatch**: `e.created_at >= :since` (NOW()-1h) гарантирует pruning партиций alert_events. Sweep использует `e.created_at >= :since` (hours), тоже с pruning.

5. **ACL-слои**: любая команда/callback требует `find_recipient`. Money-действия (`dis`, `ereco`, `plan`, `dr_ok`, `/pause`, `/resume`, `/record_plan`, `/stop_record`) — только `role='owner'`. `/autostart on/off/HH:MM` — тоже owner. `/autostart` без аргументов — read-only, любой recipient. `/spy` — любой recipient (navigates live browser: намеренно).

6. **Sentinel и rollback**: при провале TG `send_message` claim удаляется (`DELETE WHERE id=claim_id`) → следующий sweep может повторить отправку. При успехе UPDATE message_id — двухфазная запись (sentinel → реальный).

7. **Hot-reload токена**: poller проверяет `cfg.bot_token != last_token` каждые 60s; `worker_notify._client_cache` кешируется по токену и сбрасывается при ротации.

8. **Хрупкие места**:
   - Если scan-цикл занимает > 1 часа, `dispatch_pending_alerts` пропустит события, вставленные > 1h назад (since_dt window). На практике нереально, но условие нигде не проверяется.
   - `sweep_orphan_alerts` вызывается после каждого scan-цикла — при N recipients делает N SQL-запросов.
   - `worker_notify` dedup: GET + SET NX (не атомарная операция). Два параллельных вызова с одним `dedup_key` могут оба пройти проверку GET и оба отправить сообщение до SET NX.
