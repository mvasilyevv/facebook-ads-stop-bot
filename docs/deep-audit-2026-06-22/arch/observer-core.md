# Observer ядро: scan → FSM → rules → outbox

Глубокий аудит 2026-06-22. Подсистема: `core/observer/`, `core/rules/`, `core/scanner/`.

## Назначение

Observer-ядро — это money-критичный конвейер, который из одного scan-цикла Ads Manager
(набор `ScannedAdRow`) делает четыре вещи строго в порядке:

1. **Детект/нормализация** — `ScannedAdRow` (контракт TS-сканер → Python).
2. **Оценка стоп-правил** — `core/rules/evaluator.py` (7 правил, WARNING=80% / STOP, funnel-лесенка, frequency-anomaly).
3. **FSM** — чистый `decide()` решает переход состояния (`normal→warning_sent→stop_sent→claimed→disabled`).
4. **Персист + outbox** — `writers.py` пишет каталог + метрики + FSM + alert_events, и при auto-stop кладёт `meta_api_mutation pause_ad` в `task_queue`.

Ядро НЕ исполняет mutation само (это делает meta_api_worker) и НЕ доставляет алерты (это
alert_dispatcher). Оно только детектит, решает и кладёт в outbox.

## Компоненты

| Файл | Роль | Ключевые функции |
|------|------|------------------|
| `core/scanner/models.py` | Контракт строки скана | `ScannedAdRow` (frozen dataclass, 30+ полей) |
| `core/observer/queries.py` | Чтение из БД (raw SQL) | `load_active_offers`, `load_alert_state_by_fb_ad_id`, `match_offer_for_ad`, `campaign_matches_owner`, `parse_owner_tags`, `load_observer_config`, `load_scanning_enabled` |
| `core/observer/pipeline.py` | Оркестратор одного цикла | `process_scan_rows`, `_process_one_row`, `build_rule_context`, `_suppress_emit` |
| `core/rules/types.py` | Контекст и результат правил | `RuleContext` (предвычисляет пороги в `__post_init__`), `RuleHit`, `RuleEvaluation` |
| `core/rules/evaluator.py` | Движок правил | `evaluate_stop_rules`, `_evaluate_funnel_ladder`, `_evaluate_*_stage`, `_evaluate_frequency_anomaly` |
| `core/rules/frequency_analyzer.py` | Data-driven порог частоты | `compute_frequency_threshold`, `apply_recommended_threshold` (вне горячего пути) |
| `core/observer/state_machine.py` | Чистый FSM | `decide(FsmInput)→FsmTransition`, `should_reopen_disabled`, `should_sync_disabled` |
| `core/observer/writers.py` | Запись в БД + outbox | `upsert_catalog_hierarchy`, `insert_metrics`, `apply_fsm_transition`, `maybe_create_disable_task`, `reopen_reactivated_alert_state`, `mark_disabled_when_offline`, `reset_alert_state_after_*` |
| `core/observer/accounts.py` | Резолв scan set кабинетов | `resolve_scan_account_ids`, `allowlist_blocks_scan`, `normalize_account_id` |
| `core/observer/runtime.py` | Чтение Redis `observer:runtime` | `read_observer_runtime`, `_normalize_status` |
| `core/observer/adaptive_interval.py` | Адаптивный интервал | `select_scan_mode`, `compute_adaptive_interval`, `clamp_interval` |

Точка входа подсистемы — `process_scan_rows`, которую зовёт `apps/observer_worker/main.py::_run_account_scan`.

## Последовательности вызовов

### Главный цикл (один кабинет)

```
observer_worker.run_one_cycle
  └─ load_observer_config            (is_scanning_enabled? → paused)
  └─ resolve_scan_account_ids        (union offers.ad_account_ids)
  └─ для каждого кабинета: _run_account_scan
       ├─ _begin_scan_run            → scan_id (CTE, атомарно)
       ├─ gate.run_one_scan          (gRPC RunScanCycle → list[ScannedAdRow])
       ├─ process_scan_rows(rows, scan_id, owner_tag, ad_account_id)   ← ЯДРО
       ├─ dispatch_pending_alerts    (если были emit'ы)
       ├─ sweep_orphan_alerts
       └─ _finish_scan_run + _publish_scan_finished
```

### process_scan_rows (ядро)

```
process_scan_rows(rows)
  ├─ load_active_offers(engine)                         # 1 SQL
  ├─ load_alert_state_by_fb_ad_id(fb_ids)               # 1 SQL (batch FSM-снимок)
  ├─ load_external_deposits_batch(fb_ids)               # 1 SQL (AdSet.pro, 24h окно)
  └─ для каждой row: _process_one_row (try/except — падение одной не рвёт цикл)
```

### _process_one_row (per-ad)

```
_process_one_row(row)
  ├─ campaign_matches_owner?                 # owner-scoping; чужие → return (невидимы)
  ├─ match_offer_for_ad                       # word-boundary, самый длинный код
  │    └─ нет оффера → upsert_catalog + insert_metrics → return
  ├─ upsert_catalog_hierarchy                 # TX#1: campaign→adset→ad upsert
  ├─ insert_metrics                           # TX#2: ad_metrics (ON CONFLICT (ad_id,cycle_ts))
  ├─ build_rule_context(offer, external_deposits, frequency, impressions, reach)
  ├─ evaluate_stop_rules(row, ctx)            # funnel + frequency → RuleEvaluation
  ├─ reopen/sync disabled (delivery_status):
  │    ├─ should_reopen_disabled → reopen_reactivated_alert_state  # TX (disabled→normal)
  │    └─ should_sync_disabled   → mark_disabled_when_offline      # TX → return
  ├─ decide(FsmInput)                         # чистый FSM → FsmTransition
  ├─ snooze-check: snoozed_until > cycle_ts → _suppress_emit       # emit=False, task=False
  ├─ apply_fsm_transition                     # TX#3: upsert ad_alert_state + INSERT alert_events
  └─ maybe_create_disable_task                # TX#4: meta_api_mutation pause_ad в task_queue
```

### Funnel-лесенка (evaluator)

```
_evaluate_funnel_ladder(row):
  external_deposits >= 1   → _evaluate_deposit_stage      (spend 70-90% CPA)
  registrations >= 1       → _evaluate_registration_stage (CPR + regs_no_dep + spend_no_dep)
  leads >= 1               → _evaluate_lead_stage         (CPL + CPR guardrail)
  else (clicks/нет)        → _evaluate_click_stage        (CPC + CPL guardrail)

+ независимо: _evaluate_frequency_anomaly(ctx)
final = _pick_highest_priority_hit(funnel_hit, freq_hit)   # STOP > WARNING
```

ВАЖНО: лесенка выбирает РОВНО ОДНУ ступень по глубине воронки. Глубже сигнал — выше «доверие»,
поверхностные guardrail'ы не оцениваются (deposit → CPC/CPL/CPR пропускаются).

## Зависимости

**От чего зависит ядро (импорты):**
- `core/adset_pro/queries.load_external_deposits_batch` — внешние депозиты (источник истины по депозитам).
- `core/meta_api/queue.create_mutation_task` + `core/meta_api/schemas.MetaMutationPayload` — outbox auto-stop (lazy import в writers).
- `core/domain.AlertStage`, `core/rules/labels`.
- SQLAlchemy `AsyncEngine` (raw `text()` SQL, без ORM-моделей).

**Что зависит от ядра:**
- `apps/observer_worker/main.py` — единственный продакшн-вызыватель `process_scan_rows`.
- `apps/meta_api_worker` (через `core/meta_api/fsm_sync`) — приводит `ad_alert_state` после mutation, использует те же writer-reset функции.
- `apps/enable_recommendation_worker` — повторно зовёт `evaluate_stop_rules`/`determine_enable_recommendation_level`.
- `core/dashboard/snapshot.py`, API-роутеры — читают `ad_alert_state`/`ad_metrics`/`alert_events` (запись — только ядро).

**Контракты:**
- `ScannedAdRow` — главный неизменяемый контракт (TS-сканер → ядро). При новом источнике (Meta API) — отдельный `MetaApiAdRow` + adapter.
- `FsmInput`/`FsmTransition` — контракт между pipeline и state_machine.
- `observer:runtime` Redis-ключ — контракт writer (observer_worker) ↔ reader (`runtime.py`, health_details).

## Потоки данных

| Структура / хранилище | Откуда → куда | Трансформация |
|------------------------|---------------|---------------|
| `ScannedAdRow` | gRPC RunScanCycle → pipeline | `_row_to_metrics_dict` → плоский dict метрик |
| `ad_metrics` (partitioned by cycle_ts) | insert_metrics | КУМУЛЯТИВНЫЙ snapshot, ON CONFLICT (ad_id,cycle_ts) DO NOTHING |
| `ad_alert_state` (FSM) | apply_fsm_transition upsert | WHERE-guard NOT IN (claimed,disabled); snooze сброс при →normal |
| `alert_events` (partitioned by created_at) | apply_fsm_transition INSERT (append-only) | только при emit; `metrics_json._hits` = свёрнутые пороги для renderer |
| `task_queue` (meta_api_mutation) | maybe_create_disable_task | idempotency_key=`auto:pause_ad:{fb_ad_id}:{open_token}`, status=pending, max_attempts=15 |
| `fb_campaigns/fb_adsets/fb_ads` | upsert_catalog_hierarchy | COALESCE-upsert, identity=fb_campaign_id (миграция 0020) |
| `adsetpro_postback_events` (partitioned by received_at) | load_external_deposits_batch | COUNT по 24h окну → `{fb_ad_id: int}` |
| `observer:runtime` Redis | _publish_runtime_status | worker_status+status (нормализация scanning/idle/dispatch→running) |

## Внешние взаимодействия

- **Postgres** — всё чтение/запись ядра (raw SQL через AsyncEngine). Партиционированные: `ad_metrics`, `alert_events`, `scan_runs`, `adsetpro_postback_events` — каждый горячий запрос фильтрует по партиционному ключу.
- **Redis** — `observer:runtime` (статус), pubsub `fb_agent:scan:finished` (пишет worker, не ядро).
- **gRPC (browser-agent)** — RunScanCycle (вне ядра, в gate); ядро получает уже готовые `ScannedAdRow`.
- **Telegram** — НЕ напрямую; ядро только кладёт `alert_events`, доставляет alert_dispatcher.
- **Meta Marketing API** — НЕ напрямую; ядро кладёт `task_queue` outbox, исполняет meta_api_worker.
- **AdSet.pro** — через `adsetpro_postback_events` (депозиты как защита от ложного STOP).

## Инварианты

1. **Funnel-лесенка выбирает ровно одну ступень** — глубочайший сигнал воронки выигрывает; поверхностные guardrail'ы при наличии депозита/реги не оцениваются.
2. **STOP побеждает WARNING** в `_pick_highest_priority_hit` и в `decide()`.
3. **Депозит — только из AdSet.pro** (`ctx.external_deposits`); Meta `row.deposits` в deposit-логике игнорируется.
4. **idempotency_key auto-stop привязан к open_token инцидента** — одна pause-задача на инцидент; повтор STOP → UNIQUE conflict → no-op.
5. **open_token живёт весь инцидент** — новый uuid4 только при старте (`normal→...`); при эскалации `warning_sent→stop_sent` сохраняется (старые inline-кнопки валидны); обнуляется при →normal.
6. **observer не затирает терминальные состояния** — `apply_fsm_transition` ON CONFLICT WHERE `alert_state NOT IN (claimed,disabled)`.
7. **snooze сбрасывается при закрытии инцидента** (→normal) в apply_fsm_transition (H1-фикс) — чтобы устаревший снуз не подавил новый STOP.
8. **owner-scoping**: при заданном owner_tag чужие кампании невидимы (не пишем метрики, не оцениваем, не дизейблим). **Хрупкое место**: при NULL owner_tag — видны ВСЕ (включая чужие в мульти-кабинетном shared-кабинете).
9. **reopen/sync disabled с time-guard 15 мин** — защита от лага Meta effective_status на свежем disable.
10. **Идемпотентность по (ad_id, cycle_ts)** в ad_metrics и по idempotency_key в task_queue.

**Хрупкие границы (детали — в findings):**
- snooze подавляет НЕ только emit, но и auto-stop disable task (`_suppress_emit` зануляет `create_disable_task`) — расходится с документированной семантикой snooze («не отключает рекламу, только заглушает уведомления»).
- per-ad запись разбита на 4 отдельных транзакции (catalog/metrics/fsm/task) — не атомарна; восстановление частично закрыто recovery-путём `stop_sent→stop_sent`.
- `frequency_outlier_cap=10.0` хардкод — частота выше cap гасит frequency-правило целиком.
- multi-cabinet + NULL owner_tag — нет guard'а, защищающего от auto-stop чужих ads.
