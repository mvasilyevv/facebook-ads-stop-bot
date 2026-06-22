# Архитектура: Вспомогательные воркеры

> Дата: 2026-06-22 | Ревьюер: deep-audit subagent

---

## Назначение

Семь вспомогательных воркеров отвечают за периодические и событийно-управляемые задачи, не входящие в основной путь авто-стопа. Ни один из них не находится на критическом пути «скан → алерт → отключение» (это observer + meta_api_worker). Тем не менее они влияют на деньги косвенно: digest_scheduler и enable_recommendation_worker используются для ручного управления включением/отключением рекламы; cleanup_worker управляет партициями, от которых зависит надёжность хранения метрик.

---

## Компоненты и роли

| Воркер | Файл | Триггер | Ключевая функция |
|---|---|---|---|
| `digest_scheduler` | `apps/digest_scheduler/main.py` | Таймер (каждые 60с, send window = 09:00+ UTC) | `run_one_tick` → `build_digest` → `render_digest` → TG рассылка |
| `cleanup_worker` | `apps/cleanup_worker/main.py` + `worker.py` | Таймер (04:00 UTC) | `run_once`: DROP старых партиций + DELETE по retention + CREATE next-month партиций |
| `tracker_aggregator_worker` | `apps/tracker_aggregator_worker/main.py` + `worker.py` | Таймер (каждые 300с) | `aggregate_postback_events`: absolute-recompute per (ad_id, country, day) |
| `enable_recommendation_worker` | `apps/enable_recommendation_worker/main.py` | Таймер (каждые 300с) | `run_once`: кандидаты → метрики → `should_recommend` → INSERT + TG |
| `creator_worker` | `apps/creator_worker/main.py` | Поллинг `task_queue` (task_type=`plan_run`) | `process_one_task` → gRPC `RunPlan` stream |
| `creator_recorder` | `apps/creator_recorder/main.py` | Redis pubsub (`fb_agent:creator:record_*`) | `handle_record_start/stop` → gRPC `StartRecording/StopRecording` → INSERT `creator_plans` |
| `telegram_poller` | `apps/telegram_poller/main.py` | Telegram long-poll (timeout=25s) | `handle_update` → dispatcher → доменные handlers |

---

## Последовательности вызовов

### digest_scheduler

```
main_loop
  ├── heartbeat_loop (parallel, Redis SET каждые 30с)
  └── tick_loop (каждые 60с)
        └── run_one_tick(engine, redis, factory, now, window)
              ├── is_in_send_window(now, window)  [pure, UTC-aware]
              ├── redis.get("digest:sent:YYYY-MM-DD")  [dedup]
              ├── load_telegram_config(engine)
              ├── load_active_recipients(engine)
              ├── build_digest(engine, day_start_utc=now)
              │     ├── _count_alerts_by_stage  [alert_events, partition-pruned]
              │     ├── _count_disable_tasks   [task_queue, no partition]
              │     ├── _count_active_offers   [offers]
              │     ├── _count_active_ads_normal [fb_ads + ad_alert_state]
              │     └── _top_ads_and_total_spend [ad_metrics, DISTINCT ON + CTE]
              ├── render_digest(payload)  [pure, HTML]
              ├── _send_digest_to_recipients(client, recipients)  [TG API, per-recipient]
              └── redis.set("digest:sent:YYYY-MM-DD", "1", ex=26h, nx=True)
```

**Catch-up семантика**: `is_in_send_window` открыто с 09:00 UTC до 23:59 UTC того же дня. Redis-ключ с датой UTC — единственная защита от повтора.

---

### cleanup_worker

```
main_loop
  ├── heartbeat_loop (parallel)
  └── sleep until 04:00 UTC → run_once(engine, media_root)
        ├── load_policy(engine)  [system_config]
        ├── drop_old_partitions(engine, policy)  → DROP TABLE IF EXISTS {partition}
        ├── create_next_partition_if_missing(engine)  → CREATE TABLE IF NOT EXISTS
        ├── delete_task_queue_completed(engine, policy)
        ├── delete_enable_recommendations(engine, policy)
        ├── delete_expired_invites(engine, policy)
        ├── delete_old_cabinet_archives(engine, policy)
        ├── delete_old_ad_library_scans(engine, policy)
        ├── delete_orphan_ad_library_ads(engine, policy)
        ├── cleanup_orphan_media_files(media_root, db_paths)  [sync, run_in_executor]
        └── write_audit(engine)  [system_config INSERT ON CONFLICT]
```

**Важно**: cleanup_worker НЕ имеет catch-up семантики. Если пропустил 04:00 UTC — следующий запуск через 24ч.

---

### tracker_aggregator_worker

```
main_loop
  ├── heartbeat_loop (parallel)
  └── loop каждые 300с:
        └── run_once(engine, lookback=2h)
              ├── _utc_day_bounds(window_start, window_end)  [floor/ceil UTC-дней]
              └── aggregate_postback_events(engine, day_floor, day_ceil)
                    └── SQL: WITH normalized AS (...) INSERT INTO tracker_aggregate
                              ON CONFLICT DO UPDATE SET = EXCLUDED.*  [absolute recompute]
```

**Идемпотентность**: абсолютный пересчёт (не += инкремент). Повторный прогон с тем же окном: UPSERT перезаписывает те же значения — деньги не задваиваются.

---

### enable_recommendation_worker

```
main_loop
  ├── heartbeat_loop (parallel, каждые 30с, важно: цикл 300с > TTL 60с)
  └── loop каждые 300с:
        └── run_once(engine, redis, tg_client)
              ├── load_scanning_enabled(engine)  [стоп-скан → пропуск цикла]
              ├── fetch_candidates(engine, limit=50)
              │     └── SQL: JOIN ad_alert_state + fb_ads + offers + offer_rules
              │           WHERE state IN ('stop_sent','disabled')
              │           AND last_transition_at < NOW() - COOLDOWN
              │           AND NOT EXISTS ad_auto_enable_disabled
              └── for each candidate:
                    ├── is_recently_recommended(redis)  [enable_reco:last:{ad_id} TTL 6h]
                    ├── fetch_metrics_since(engine, ad_id, since)
                    │     └── SQL: WHERE ad_id=:aid AND cycle_ts > :since  [single partition pruned]
                    ├── should_recommend(state, snoozed, now, metrics, offer_thresholds)
                    ├── insert_recommendation(engine, idem_key=ad_id:transition_ts)
                    │     └── ON CONFLICT (idempotency_key) DO NOTHING RETURNING id
                    ├── send_alert(tg_client, candidate, decision)
                    │     └── load_active_recipients + TG sendMessage per recipient
                    └── mark_recommended(redis, ad_id)  [NX, только при sent=True]
```

---

### creator_worker

```
main_loop
  ├── heartbeat_loop (parallel)
  └── task_loop:
        └── loop:
              ├── claim_next_task(engine, task_type='plan_run')  [FOR UPDATE SKIP LOCKED]
              └── process_one_task(engine, task, client)
                    ├── _parse_plan_id(payload)  [ValueError → mark_failed]
                    ├── load_plan(engine, plan_id)  [None или is_archived → mark_failed]
                    └── _execute_plan_stream(client, plan_json, variables_json)
                          ├── client.run_plan(...)  [gRPC stream → PlanEvent*]
                          └── aggregate: ok/steps/duration/failed_step/checkpoints
                    → result.ok → mark_succeeded / mark_failed
```

---

### creator_recorder

```
main_loop
  ├── heartbeat_loop (parallel)
  └── pubsub_loop(redis, engine, browser_client, stop)
        └── subscribe("fb_agent:creator:record_start", "fb_agent:creator:record_stop")
              └── get_message(timeout=1s) → _process_message
                    ├── record_start → handle_record_start → client.start_recording()
                    └── record_stop  → handle_record_stop
                          ├── client.stop_recording()  → (stopped, plan_json, steps)
                          ├── _insert_plan(engine, name, steps, variables)
                          │     └── ON CONFLICT (uq_creator_plans_name_active) → retry с UTC-suffix
                          └── tg_client.send_message(recipient_id)  [confirmation]
```

**Схема публикации**: `telegram_poller` получает `/record_plan` → publish в Redis → `creator_recorder` потребляет.

---

### telegram_poller

```
main_loop(db_url)
  ├── heartbeat_loop (parallel Redis SET, отдельный redis клиент)
  └── loop (long-poll, timeout=25с):
        ├── load_telegram_config(engine)  [раз в 60с или при client=None]
        ├── touch_poller_heartbeat(engine)  [БД heartbeat раз в 30с]
        ├── client.get_updates(offset+1, timeout=25s)
        └── for update in updates:
              ├── handle_update(engine, client, update, redis_pubsub)
              │     ├── callback_query → _dispatch_callback_query
              │     │     ├── find_recipient(engine)  [ACL check]
              │     │     └── → handle_dis / handle_enable_reco / handle_draft / handle_plan
              │     └── message → command dispatch
              │           ├── /start  → handle_start  [onboarding]
              │           ├── /spy    → handle_spy  [Ad Library pipeline]
              │           ├── /pause /resume → handle_bulk_toggle  [meta_api draft]
              │           ├── /record_plan → publish Redis record_start
              │           ├── /stop_record → publish Redis record_stop
              │           └── /plans → список creator_plans с inline кнопками
              └── save_poller_offset(engine, offset)  [per update]
```

---

## Зависимости

### Что потребляет подсистема

| Ресурс | Потребители |
|---|---|
| PostgreSQL (async) | Все 7 воркеров |
| Redis | digest_scheduler, enable_reco, cleanup (heartbeat), tracker_agg (heartbeat), creator_worker (heartbeat), creator_recorder (pubsub), telegram_poller (heartbeat + pubsub) |
| gRPC browser-agent (50051) | creator_worker, creator_recorder |
| Telegram Bot API (HTTP) | digest_scheduler, enable_reco, creator_recorder, telegram_poller |
| Filesystem (data/ad_library_media) | cleanup_worker |
| `core.adset_pro.aggregator` | tracker_aggregator_worker |
| `core.enable_reco.analyzer` | enable_reco_worker |
| `core.tasks.queue` | creator_worker |
| `core.telegram.*` | digest_scheduler, enable_reco, telegram_poller |

### Что зависит от подсистемы

- **observer_worker**: потребляет `tracker_aggregate` для `external_deposits` через `load_external_deposits_batch` (evaluator.py → rules/evaluator.py). Данные о депозитах влияют на stop-решение.
- **meta_api_worker**: потребляет `enable_recommendations.promoted_to_task_id` и `task_queue` с `task_type='plan_run'`.
- **FastAPI**: читает `system_config.cleanup_runs` / `tracker_aggregator_runs` для health details; `enable_recommendations` через API роутер.
- **health_watchdog**: мониторит heartbeats всех 7 воркеров.

---

## Потоки данных

### Tracker aggregator
```
adsetpro_postback_events (partitioned, received_at)
    → [window = day_floor..day_ceil по UTC]
    → tracker_aggregate (ad_id, country, day) [UPSERT absolute]
    → load_external_deposits_batch (observer pipeline)
    → RuleContext.external_deposits
    → evaluator: стоп-правила
```

### Enable recommendation
```
ad_alert_state (stop_sent/disabled, cooldown)
    → fetch_candidates
    → ad_metrics (cycle_ts > since, per ad_id)
    → should_recommend (pure)
    → enable_recommendations [INSERT idempotent]
    → TG alert (inline кнопка ereco:<fb_ad_id>)
    → telegram_poller: handle_enable_reco_callback
    → create_mutation_task (activate_ad) → task_queue
    → meta_api_worker → Meta API
```

### Digest
```
alert_events (partitioned) + task_queue + ad_metrics (partitioned)
    → build_digest (rolling 24h window от now назад)
    → render_digest (HTML)
    → TG рассылка всем active recipients
```

### Creator
```
TG /record_plan → pubsub record_start
    → creator_recorder → gRPC StartRecording
    → TG /stop_record → pubsub record_stop
    → creator_recorder → gRPC StopRecording → plan_json
    → creator_plans [INSERT + UTC-suffix retry]

TG /plans → inline-кнопка plan:<uuid>
    → task_queue (plan_run) [creator_worker поллит]
    → gRPC RunPlan stream → PlanEvent*
    → mark_succeeded / mark_failed
```

---

## Внешние взаимодействия

| Сервис | Протокол | Воркер | Назначение |
|---|---|---|---|
| Telegram Bot API | HTTPS (httpx) | telegram_poller, digest_scheduler, enable_reco, creator_recorder | Long-poll updates, send messages |
| PostgreSQL | asyncpg | Все | Данные состояния и метрик |
| Redis | redis-py async | Все (heartbeat, дедуп, pubsub) | TTL-ключи, pubsub-каналы |
| gRPC browser-agent (:50051) | gRPC (grpcio) | creator_worker, creator_recorder | Vision CDP: запись и воспроизведение планов |

---

## Инварианты и контракты

### digest_scheduler
- **Redis-дедуп**: ключ `digest:sent:YYYY-MM-DD` (TTL 26h) гарантирует не более одного дайджеста в UTC-сутки. При Redis down → возврат "already_sent" (conservative miss).
- **No-TG-config**: если токен не настроен — флаг НЕ ставится (воркер дошлёт после настройки).
- **No-recipients**: флаг ставится (не долбим пустоту).
- **Catch-up**: окно [09:00, 23:59] UTC. При рестарте в 12:00 digest доставится.
- **ХРУПКО**: частичный сбой рассылки (1 из N recipient'ов) — флаг ставится, пропущенный recipient не получит за этот день.

### cleanup_worker
- **Идемпотентность**: `DROP TABLE IF EXISTS` и `CREATE TABLE IF NOT EXISTS` — safe повтор.
- **Независимость шагов**: каждая операция в отдельной транзакции, сбой одного не откатывает другие.
- **НЕТ catch-up**: пропущенный запуск не компенсируется.
- **ХРУПКО**: DROP без CASCADE — если появятся FK от другой таблицы к партиции (маловероятно по текущей схеме).

### tracker_aggregator_worker
- **Absolute recompute**: `ON CONFLICT DO UPDATE SET = EXCLUDED.*` (не += инкремент). Идемпотентен.
- **Фильтры**: `fb_ad_fk IS NOT NULL`, `is_duplicate = FALSE`, `char_length(country) = 2` — исключают «мусорные» события.
- **Partition pruning**: фильтр по `received_at >= :day_floor AND received_at < :day_ceil` — обязательный партиционный ключ.
- **ХРУПКО**: concurrent запуск двух воркеров → оба пишут одни и те же значения (UPSERT корректен), дублирования нет.

### enable_recommendation_worker
- **Redis-дедуп**: `enable_reco:last:{ad_id}` TTL 6h, NX. Ставится ТОЛЬКО при успешной отправке алерта.
- **DB-идемпотентность**: `idempotency_key = f"enable_reco:{ad_id}:{int(last_transition_at.timestamp())}"` — уникален per (ad, FSM transition).
- **ХРУПКО**: если TG send fails после успешного INSERT → Redis NX не ставится → следующий цикл: `insert_recommendation` returns None (ON CONFLICT DO NOTHING) → `skipped_decision` → алерт теряется навсегда для этой FSM-транзиции.
- **ХРУПКО**: `_aggregate_spend` суммирует кумулятивные снимки ad_metrics (не дельты), что завышает spend для Rule 1.

### creator_worker / creator_recorder
- **gRPC circuit-breaker**: 3 ошибки → OPEN 60с → BrowserUnavailableError → requeue_for_retry.
- **Pubsub fire-and-forget**: TG poller публикует record_start/stop без подтверждения от recorder. Если recorder не запущен — сообщение теряется.
- **TG client не ротируется**: creator_recorder строит `tg_client` один раз при старте из DB. Смена bot_token требует рестарта процесса.

### telegram_poller
- **Idle-режим**: при отсутствии token — продолжает работу (heartbeat активен), не падает.
- **Hot token reload**: раз в 60с или при client=None — перечитывает telegram_config из БД.
- **offset persistence**: `save_poller_offset` после каждого батча updates (при сбое — повтор безопасен: update_id дедупится Telegram 24h).
