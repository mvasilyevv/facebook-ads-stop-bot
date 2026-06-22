# Карта архитектуры — Money-критичные воркеры

Подсистема: `apps/observer_worker/`, `apps/reconciler_worker/`, `apps/health_watchdog/`,
`apps/cabinet_scheduler/`, `apps/meta_api_worker/` (life-cycle).
Дата: 2026-06-22. Read-only разбор.

## Назначение

Пять воркеров образуют money-критичный контур «детект → решение → исполнение → надзор»:

- **observer_worker** — сканирует Ads Manager (am_tabular через Vision/gRPC), гоняет FSM,
  пишет метрики, создаёт outbox-задачи авто-стопа и диспетчит TG-алерты. Источник
  money-решений (STOP-правило → pause_ad).
- **meta_api_worker** — единственный исполнитель money-мутаций Marketing API
  (pause/activate/budget/bulk/create). Поллит `task_queue` (`task_type='meta_api_mutation'`),
  применяет асимметричный стоп, owner-scoping, FSM-sync и эскалации недоставленных пауз.
- **cabinet_scheduler** — автостарт кабинета по расписанию: раз в минуту в окне HH:MM UTC
  создаёт `pending bulk_status_change activate` для owner-scoped кампаний из allowlist.
- **reconciler_worker** — раз в 30с лечит застрявшие задачи: необратимые stuck → `failed`
  (без retry, иначе дубль кампании), прочие stuck `running` → `retrying` (+1 attempt),
  протухшие `draft` → `cancelled`.
- **health_watchdog** — внешний наблюдатель: heartbeat-и воркеров, свежесть `observer:runtime`,
  БД-детектор отказа канала авто-стопа (stuck pause / desync stop_sent↔ACTIVE) и
  единственный сетевой probe `GET /me` Marketing API.

Общий контракт: все воркеры пишут `worker:heartbeat:<name>` (TTL 60s), heartbeat поднимается
ОТДЕЛЬНЫМ таском СРАЗУ после Redis (до тяжёлой инициализации), graceful shutdown по SIGTERM/SIGINT.

## Компоненты

### observer_worker (`apps/observer_worker/main.py`, 1226 строк)
- `main_loop` — бесконечный цикл: gate-factory → pubsub-listener (trigger/cabinet_day/restart) →
  `run_one_cycle` → адаптивный sleep с runtime-refresh.
- `run_one_cycle` — резолвит scan set кабинетов (`resolve_scan_account_ids`), готовит вкладки
  (`_prepare_workspace`), последовательно сканирует каждый кабинет (`_run_account_scan`).
- `_run_account_scan` — `_begin_scan_run` (CTE-атомарный INSERT в partitioned `scan_runs`) →
  `gate.run_one_scan` → `process_scan_rows` (FSM+метрики+outbox) → `dispatch_pending_alerts` +
  `sweep_orphan_alerts` → `_finish_scan_run` (UPDATE) → `_publish_scan_finished`.
- Layer 3 degraded-алерт (`_maybe_alert_degraded`) — N подряд error-циклов → один TG-CRITICAL.
- `_publish_runtime_status` — пишет `observer:runtime` с двумя статусами (`worker_status` детальный
  + `status` нормализованный running|paused) — контракт writer↔reader из Round 10.

### meta_api_worker (`apps/meta_api_worker/main.py`, 725 строк)
- `task_loop` — claim (`claim_pending_task` → FOR UPDATE SKIP LOCKED) → `process_one_task`; на idle
  прогревает account_tz и эскалирует недоставленные паузы.
- `process_one_task` — парсинг payload → асимметричный стоп-гейт (`_is_activating_mutation` +
  `load_scanning_enabled`) → owner-scoping (`check_mutation_ownership`) → `execute_mutation`
  (`dispatch_mutation`) → `mark_task_succeeded` → `sync_fsm_after_mutation` → `record_autostop_success`.
- Маршрутизация ошибок: permanent→fail, temporary→requeue, irreversible kinds→fail (риск дубля),
  голый ValueError/Exception→requeue (кроме irreversible). Финальный провал money-мутации
  (pause/bulk) → `_alert_money_fail`.

### cabinet_scheduler (`apps/cabinet_scheduler/main.py`, 342 строки)
- `tick_loop` → `run_one_tick`: gate `load_scanning_enabled` (шаг 0, money-стоп) → конфиг →
  `is_in_autostart_window` → Redis GET done-key → owner_tag+allowlist из observer_config →
  `resolve_owner_ad_ids_by_campaign_ids` → `create_mutation_task(pending bulk activate)` →
  `_trigger_observer_scan` → `_set_autostart_done` (только при started).

### reconciler_worker (`apps/reconciler_worker/{main,worker}.py`)
- `run_once`: `fail_irreversible_stuck` (ДО reconcile) → `reconcile_stuck_running`
  (exclude irreversible) → `cancel_old_drafts`. Тонкая env-обёртка над `core.tasks.queue`.

### health_watchdog (`apps/health_watchdog/main.py`, 812 строк)
- `check_loop` (60s): `check_worker_heartbeats` + `check_observer_runtime` +
  `check_autostop_channel` (БД: stuck pause + desync) + `_publish_health_updated`.
- `meta_probe_loop` (300s): `check_meta_api_channel` → реальный `GET /me` через browser-agent →
  пишет `meta_api:channel:health` в Redis → CRITICAL при отказе с re-arm дедупом.

## Последовательности вызовов

### Авто-стоп (money-критичный путь)
```
observer.run_one_cycle
  → process_scan_rows  (FSM decide → STOP)
      → writers.maybe_create_disable_task
          → create_mutation_task(pause_ad, requested_by='bot_auto_stop', status='pending')  [task_queue]
  ... (отдельный воркер) ...
meta_api_worker.task_loop
  → claim_pending_task               [UPDATE ... FOR UPDATE SKIP LOCKED → status='running']
  → process_one_task
      → _is_activating_mutation? (pause → нет, исполняем даже на паузе)
      → check_mutation_ownership (свой ad?)
      → execute_mutation → dispatch_mutation → pause_ad (Vision page.evaluate fetch)
      → mark_task_succeeded            [WHERE status='running']
      → sync_fsm_after_mutation        (ad_alert_state → disabled)
      → record_autostop_success        (Redis re-arm)
```

### Автостарт кабинета
```
cabinet_scheduler.tick_loop (каждые 60с)
  → run_one_tick
      → load_scanning_enabled (пауза → стоп)
      → read_autostart_config + is_in_autostart_window
      → redis GET cabinet:autostart:YYYY-MM-DD (дедуп)
      → resolve_owner_ad_ids_by_campaign_ids (allowlist ∩ owner_tag ∩ is_active)
      → create_mutation_task(bulk_status_change activate, idem='autostart:{day}:activate')
      → publish fb_agent:observer:trigger
      → set_autostart_done
  ... meta_api_worker подхватывает bulk activate ...
```

### Reconcile-zombie
```
worker крашнулся в 'running' (SIGKILL/OOM/деплой)
  ... reconciler_worker (каждые 30с) ...
  run_once
    → fail_irreversible_stuck (create/duplicate → failed, НЕ retry)
    → reconcile_stuck_running (running>30m & НЕ irreversible → retrying, attempt+1)
    → cancel_old_drafts (draft>24h → cancelled)
```

### Надзор отказа канала
```
health_watchdog.check_loop (60с)        → БД-детектор (post-factum): stuck pause / desync
health_watchdog.meta_probe_loop (300с)  → активный probe GET /me → meta_api:channel:health
meta_api_worker (на каждом fail pause)  → maybe_alert_autostop_channel_down (Redis counter)
meta_api_worker (idle)                  → escalate_undelivered_autostop_pauses (per-ad)
```
Три независимых детектора отказа канала пересекаются (один инцидент → до 3 алертов разной природы).

## Зависимости

**Подсистема зависит от:**
- `core.tasks.queue` — канонический claim/mark/requeue/reconcile (единый источник attempt_count).
- `core.meta_api.queue` — outbox-обёртка для `meta_api_mutation` + ACL approve_draft.
- `core.meta_api.{ownership,fsm_sync,bulk,autostop_alert,mutations}` — owner-scoping, FSM-sync,
  резолв ad_id, эскалации, dispatch.
- `core.observer.{accounts,queries,pipeline,writers,runtime,adaptive_interval}` — scan set,
  config, FSM-пайплайн, runtime-контракт.
- `core.scheduler.cabinet_autostart` — окно/дедуп-ключ автостарта.
- `core.telegram.worker_notify` — money-нотификации (notify_owners/notify_recipients, dedup-after-send).
- `core.control.pubsub_listener` — подписки observer'а.
- Внешнее: browser-agent gRPC (:50051), Postgres (:5433), Redis (:6380), Telegram Bot API.

**От подсистемы зависят:**
- API-дашборд читает `observer:runtime`, `meta_api:channel:health`, `worker:heartbeat:*`.
- `health_details` роутер — те же ключи (контракт DEFAULT_EXPECTED_WORKERS).
- FSM (`ad_alert_state`) — observer пишет, meta_api_worker синкает после мутации.

## Потоки данных

| Структура / ключ | Где рождается | Где трансформируется / читается |
|---|---|---|
| `task_queue` row (meta_api_mutation) | observer.writers / cabinet_scheduler / TG | meta_api_worker (claim→execute→mark), reconciler (stuck), health_watchdog (stuck SQL) |
| `ScannedAdRow` | browser-agent gRPC | process_scan_rows → метрики/FSM/каталог |
| `scan_runs` (partitioned by started_at) | `_begin_scan_run` (CTE) | `_finish_scan_run` UPDATE, dashboard scan-runs |
| `ad_alert_state` (FSM) | observer pipeline | meta_api_worker sync_fsm_after_mutation; health desync-SQL |
| Redis `observer:runtime` (TTL 360s) | `_publish_runtime_status` | health_watchdog, API/UI (read_observer_runtime) |
| Redis `worker:heartbeat:<name>` (TTL 60s) | каждый воркер | health_watchdog check_worker_heartbeats |
| Redis `meta_api:channel:health` (TTL 600s) | watchdog meta_probe | health_details роутер |
| Redis `cabinet:autostart:YYYY-MM-DD` (TTL 26h) | cabinet_scheduler | дедуп автостарта |
| Redis `autostop:*` counter/dedup | meta_api_worker autostop_alert | re-arm на success |
| pubsub `fb_agent:observer:trigger` | scheduler / API scan-now | observer прерывает sleep |
| pubsub `fb_agent:scan:finished` / `task:changed` / `health:updated` | observer / meta_api / watchdog | фронт live-invalidation |

## Внешние взаимодействия

- **Postgres** — `task_queue` (outbox, не partitioned), `scan_runs`/`ad_metrics`/`alert_events`
  (partitioned), `ad_alert_state`/`fb_ads`/`fb_campaigns` (каталог+FSM), `system_config`/
  `observer_config` (конфиг), `telegram_recipients` (ACL/нотификации).
- **Redis** — heartbeat, runtime, дедупы, pubsub, счётчики автостоп-фейлов.
- **gRPC browser-agent** — observer (RunScanCycle), meta_api_worker (ExecuteGraphCall→pause/activate),
  watchdog (check_health full_probe = GET /me).
- **Telegram** — money-нотификации owner/recipients (dedup-after-send), degraded/CRITICAL алерты.
- **Meta Marketing API** — только через Vision `page.evaluate(fetch)` внутри browser-agent.

## Инварианты и контракты

1. **Единственный bump attempt_count** — только `core.tasks.queue.reconcile_stuck_running`
   (reconciler — тонкая обёртка). Дублирование = вдвое быстрее исчерпание попыток.
2. **mark_*/requeue под `WHERE status='running'`** — защита от двойного исполнения после
   reconciler-race; все возвращают bool, caller обязан проверить applied.
3. **Необратимые kinds (create/duplicate) НЕ ретраятся** — ни worker, ни reconciler; stuck → failed.
4. **idempotency_key** — UNIQUE; автостарт включает день (`autostart:{day}:activate`) → один task/день.
5. **Асимметричный стоп** — на паузе сканирования исполняются только ВЫКЛЮЧАЮЩИЕ мутации
   (pause_*/bulk pause); активирующие откладываются/отменяются.
6. **Owner-scoping last-line-of-defense** — каждая мутация сверяется с owner_tag перед исполнением;
   пустой owner_tag = фильтр выключен (ALLOW).
7. **Автостарт по пустому allowlist → ничего не включаем** (НЕ весь кабинет) — money-safety.
8. **Дедуп-после-доставки** — health_watchdog/worker_notify ставят dedup-ключ ТОЛЬКО после
   успешной отправки (сбой TG не «съедает» алерт на TTL). Хрупкость: при отсутствии TG-клиента
   дедуп для degraded/autostart всё равно может не ставиться корректно (см. findings).
9. **observer:runtime двойной контракт** (worker_status детальный + status нормализованный) —
   читатели через `read_observer_runtime`; рассогласование = `unknown` (история CRIT-2).
10. **scan_runs partition-pruning** — запросы к `scan_runs` ОБЯЗАНЫ фильтровать по `started_at`;
    `_finish_scan_run` нарушает (UPDATE по `id` без `started_at`) — см. findings.
11. **Catch-up окна** (autostart/digest) — от HH:MM до конца суток UTC; повтор блокирует Redis-ключ,
    не само окно. Хрупкость: `no_owner_ads`-путь автостарта не ставит done-ключ → ретрай+scan-trigger
    каждый тик до конца суток (см. findings).
