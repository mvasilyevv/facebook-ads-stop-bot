# FB Stop Bot — сквозная карта системы (deep audit 2026-06-22)

Документ сшивает топологию всех 11 проаудированных подсистем в единую картину: сервисы, money-канал
жизненного цикла объявления, контракты между слоями и зависимости. Источник для риск-синтеза в
`99-risk-synthesis.md`.

---

## 1. Карта сервисов

Система — это набор stateless-воркеров вокруг трёх шин состояния (Postgres / Redis / outbox `task_queue`),
один «руки в браузере» компонент (Node.js gRPC browser-agent) и два фронта. Бизнес-логику детекта и
авто-стопа держит `core/observer/*`; **исполнение всех записей в Meta идёт ИСКЛЮЧИТЕЛЬНО через
browser-agent изнутри Vision-сессии** (Marketing API никогда не через httpx — EAA-токен привязан к
session-context).

```
                        ┌─────────────────────────── ВНЕШНИЕ ───────────────────────────┐
                        │  Facebook Ads Manager / Graph API   Telegram Bot API   AdSet.pro │
                        └────────▲───────────────▲────────────────▲──────────────▲────────┘
                                 │ page.evaluate(fetch)            │ httpx        │ postback HTTP
                                 │ (Vision-сессия)                 │              │
                    ┌────────────┴───────────┐                    │              │
                    │  browser-agent (Node)  │                    │              │
                    │  gRPC :50051           │                    │              │
                    │  Scanner / MetaApi /   │                    │              │
                    │  Session / Creator     │                    │              │
                    └────▲──────────────▲────┘                    │              │
        RunScanCycle     │              │ ExecuteGraphCall        │              │
        (am_tabular)     │              │ (pause/activate/bulk)   │              │
                         │              │                         │              │
   ┌─────────────────────┴──┐    ┌──────┴───────────────┐  ┌──────┴─────┐  ┌─────┴──────────┐
   │  observer_worker       │    │  meta_api_worker     │  │ telegram_  │  │ FastAPI         │
   │  scan→FSM→outbox       │    │  outbox→Meta→FSM-sync│  │ poller     │  │ apps/api        │
   └───┬───────────────┬────┘    └──────┬───────────────┘  └─────┬──────┘  │ + /ws bridge    │
       │ insert metrics│ create          │ claim/mark            │ callbacks└──┬───────────┬──┘
       │ alert_events  │ pause_ad task   │ task_queue            │ create task │           │
       ▼               ▼                 ▼                       ▼             │           ▼
   ┌────────────────────────────────────────────────────────────────────┐    │   ┌──────────────┐
   │                          POSTGRES 16                                │    │   │  фронты:     │
   │  catalog(offers→campaigns→adsets→ads)  ad_alert_state(FSM)          │    │   │  web (X-API) │
   │  ad_metrics▣  alert_events▣  scan_runs▣  task_queue(outbox)         │    │   │  mini(TMA)   │
   │  adsetpro_postback_events▣  tracker_aggregate  meta_api_audit▣      │    │   └──────────────┘
   │  ad_library_snapshot▣   (▣ = partitioned RANGE)                     │    │
   └────────────────────────────────────────────────────────────────────┘    │
       ▲                          ▲                ▲                           │
       │ heartbeat/runtime/pubsub │                │ heartbeat/probe           │
   ┌───┴──────────┐  ┌────────────┴──────┐  ┌───────┴────────┐  ┌──────────────┴─────────────┐
   │ REDIS        │  │ cabinet_scheduler │  │ health_watchdog│  │ aux workers:               │
   │ heartbeat:*  │  │ autostart bulk    │  │ probe GET /me  │  │ reconciler / cleanup /     │
   │ observer:    │  │ activate          │  │ heartbeat-check│  │ enable_reco / digest /     │
   │ runtime      │  └───────────────────┘  └────────────────┘  │ tracker_aggregator /       │
   │ pubsub chans │                                             │ creator_worker/recorder    │
   └──────────────┘                                             └────────────────────────────┘
```

**Категории воркеров:**
- **Money-контур (5):** observer_worker (детект+FSM), meta_api_worker (единственный исполнитель Meta-мутаций),
  cabinet_scheduler (автостарт bulk-activate), reconciler_worker (лечение застрявших задач),
  health_watchdog (надзор канала авто-стопа).
- **Aux (7):** digest / cleanup / tracker_aggregator / enable_recommendation / creator_worker /
  creator_recorder / telegram_poller.
- **Шлюзы:** FastAPI (`apps/api`, 17 v1-роутеров + `/ws` pubsub-мост), browser-agent (5 gRPC-сервисов).

---

## 2. Полный жизненный цикл объявления (сквозь все слои)

Последовательность money-канала от скана до авто-стопа и обратного включения. Каждая стрелка — реальный
переход в коде; слой указан в скобках.

```
SCAN
 1. observer_worker.run_one_cycle (Python)
      → resolve_scan_account_ids (per кабинет, последовательно)
      → gRPC RunScanCycle → browser-agent.index.runScanCycle (TS)
          → ensureAdsManagerPage(actId) → withPageLock(
              acquireGraphContext [снифф EAA-токена] →
              runAmScanWithContext [am_tabular + edges → ScannedAdRow])
          → toProtoRow → stream
      → Python _proto_to_row → process_scan_rows

МЕТРИКИ + FSM (core/observer, на каждую row, 4 раздельные транзакции)
 2. campaign_matches_owner (owner-scoping) → match_offer_for_ad
 3. upsert_catalog_hierarchy  [TX1: ставит is_active=TRUE, НИКОГДА не FALSE]
 4. insert_metrics            [TX2: ad_metrics — КУМУЛЯТИВНЫЙ spend за cabinet-day]
 5. build_rule_context (+ external_deposits из tracker_aggregate) → evaluate_stop_rules
      → funnel-лесенка + frequency-anomaly → STOP > WARNING
 6. decide(FsmInput) → FsmTransition  [чистый FSM: normal→warning_sent→stop_sent→claimed→disabled]
 7. snooze-gate: если snoozed_until > cycle_ts → _suppress_emit (глушит emit И create_disable_task)
 8. apply_fsm_transition       [TX3: ON CONFLICT guard NOT IN (claimed,disabled)]
 9. maybe_create_disable_task  [TX4: при STOP → outbox meta_api_mutation pause_ad,
                                idempotency=auto:pause_ad:{fb_ad_id}:{open_token}]

WARNING / STOP DELIVERY (core/telegram)
10. dispatch_pending_alerts (по scan_id, partition pruning created_at>=NOW()-1h)
      → pre-claim INSERT telegram_message_refs (sentinel message_id=0)
      → send_message → UPDATE реальным id  [дедуп на UNIQUE: 1 алерт per incident×stage×recipient]

OUTBOX → META (meta_api_worker)
11. claim_pending_task (FOR UPDATE SKIP LOCKED)
12. асимметричный стоп-гейт (на паузе исполняются только выключающие мутации)
13. check_mutation_ownership (last-line-of-defense owner-scoping)
14. dispatch_mutation → PauseAdHandler → client.execute_graph_call
      → gRPC ExecuteGraphCall → browser-agent page.evaluate(fetch POST /{ad_id}?status=PAUSED)
15. классификация ошибки: Permanent→mark_failed | Temporary/SessionUnavailable→requeue
16. mark_task_succeeded (WHERE status='running')  ← НЕ читает result['success'] (см. риск R3)
17. sync_fsm_after_mutation → reset_alert_state_after_disable_succeeded (ad_alert_state→disabled)
18. record_autostop_success / при N фейлах → channel-down CRITICAL DM

ENABLE-RECO → ACTIVATE (enable_recommendation_worker → пользователь → meta_api_worker)
19. fetch_candidates (stop_sent/disabled старше cooldown) → fetch_metrics_since
20. should_recommend [analyzer: 4 OR-правила; Rule1 на _aggregate_spend — баг R2]
21. insert_recommendation → send_alert (TG inline ereco:) ИЛИ UI-дашборд PENDING
22. подтверждение owner → create_task(meta_api_mutation activate_ad)
23. meta_api_worker → activate_ad → sync_fsm_after_mutation → ad_alert_state→normal
```

---

## 3. Ключевые контракты между слоями и где они хрупкие

| Контракт | Где живёт | Хрупкость |
|---|---|---|
| **ScannedAdRow** (TS→Python) | `am-join.buildScannedRow` → `index.toProtoRow` → `client._proto_to_row` | Поле задаётся в **трёх** местах; пропуск в любом = тихий NULL в БД (MEMORY: ScannedAdRow checklist). |
| **task_queue outbox** | `core/tasks/queue.py` | НЕ связан FK с каталогом (by design). DELETE fb_ads оставляет orphan-задачи, которые meta_api_worker исполнит вслепую по живому fb_ad_id в Meta (R1). |
| **result['success']** | `mutations/base.success_result` → `meta_api_worker.process_one_task` | worker НЕ инспектирует поле; handler с логическим провалом без exception (bulk all-failed) метится succeeded без money-fail DM (R3). |
| **Graph error code** (-1/-2/-3/190) | TS `extractGraphError` ↔ Python `core/meta_api/errors.py` | Приватный контракт; рассинхрон ломает маршрутизацию requeue/mark_failed авто-стопа. |
| **observer:runtime** (Redis) | writer пишет `worker_status`+`status`, reader через `read_observer_runtime` | Двойной контракт; рассинхрон давал `unknown` (история CRIT-2). Читать только через единую точку. |
| **heartbeat:{name}** (Redis) | 5+ воркеров ↔ `health_watchdog.EXPECTED_WORKERS` | Имена жёстко связаны; копипаста heartbeat_loop в 5 местах → риск рассинхрона (история Round 11). |
| **ad_metrics кумулятив** | `writers.insert_metrics` ↔ все агрегаторы | spend нарастает за cabinet-day, обнуляется в полночь. Naive SUM задваивает деньги; правило держится комментариями/review, не типами (R2, F-web/F-data повторы). |
| **owner-scoping** | `am-owner.ts` (TS) ↔ `core/observer/queries.py` (Python) | Ручное зеркало; расхождение = неверный скоуп. NULL owner_tag в мульти-кабе → True для ВСЕХ кампаний (R4). |
| **DEPOSIT_EVENT_TYPES** | `ingest` ↔ `aggregator` ↔ `evaluator` | Единый контракт через assert; включение `redep` в дедуп-ключ `(click_id,event_type)` подавляет легитимные повторы (R7). |
| **pubsub** (Redis) | `fb_agent:observer:trigger`, `creator:record_*` | fire-and-forget, НЕ очередь. Если подписчик не запущен в момент publish — событие теряется (creator-флоу). |
| **TMA-токен подпись** | `tma.py::_tma_secret` = `encryption_key` fallback | Ротация Fernet-ключа инвалидирует все живые TMA-сессии (R-api MID). |

---

## 4. Карта зависимостей и нарушения слоёв

**Нормальные направленные зависимости:**
- observer_worker → core/observer → core/rules → Postgres/Redis
- meta_api_worker → core/meta_api → browser-agent gRPC → Meta
- фронты → FastAPI → Postgres/Redis (read) + task_queue (write, без прямых Meta-мутаций — инвариант держится)
- aux-воркеры → свои core/* модули, общий путь рассылки TG

**Циклы и нарушения слоёв (источники хрупкости):**

1. **meta_api ↔ observer (двусторонняя связь).** `core/meta_api/fsm_sync.py` импортирует
   `core/observer/writers.reset_*`. Изменение сигнатуры reset-функций ломает FSM-sync **молча**
   (best-effort except глушит). Слой исполнения мутаций зависит от слоя детекта.

2. **FastAPI → browser-agent напрямую.** `settings_observer.refresh-campaigns` дёргает
   `BrowserAgentClient.list_campaigns` из HTTP-обработчика — ту же Vision-сессию, которую держит
   observer_worker. (Верификатор: navigate НЕ вызывается, скан через am_tabular не рвётся — риск опровергнут,
   но архитектурная связь HTTP↔Vision остаётся.)

3. **Сессионное состояние вместо per-cabinet.** browser-agent держит `netFailureStreak`/`healLevel`/
   `primaryPage` на `BrowserSession` (одной на профиль), а мульти-кабинет сканирует несколько `actId`
   последовательно. Self-heal лечит не ту вкладку и маскирует устойчиво мёртвый кабинет (R5 — два HIGH).

4. **Монотонный is_active как «фильтр живости».** Каталог ставит `is_active=TRUE` и никогда не FALSE;
   cabinet_scheduler фильтрует по нему как по «живым» ad. Реальной фильтрации по дате/last_seen нет;
   документированная `resolve_owner_ad_ids_by_dates` в коде **отсутствует** (R-money HIGH).

5. **Тройной/четверной детект отказа канала** без общего дедупа (watchdog БД + watchdog probe +
   worker channel-down + worker per-ad) → шквал разнородных алертов на один инцидент (R6).

6. **Партиции на след. месяц создаёт ТОЛЬКО cleanup_worker.** Простой воркера на стыке месяца → отказ
   INSERT во ВСЕ partitioned-таблицы (метрики/алерты/scan_runs/postback) — тихая остановка money-потока (R8).

7. **Один X-API-Key на всю систему** (ролей нет). Все write-эндпоинты FastAPI (вкл. деструктивный
   bulk-delete) доступны любому держателю ключа; ACL по owner есть только в TG/draft-пути.
