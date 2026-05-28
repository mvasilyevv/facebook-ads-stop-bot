# Backend Test Audit — Round 8

Дата: 2026-05-28
Объём: 936 тестов (498 unit + 434 integration + 4 e2e ad-library)
Файлов: 54 unit + 73 integration (всего 127 файлов), ~25 295 строк тестового кода
Цель: проверить, что тесты валидируют **смысловую логику**, а не только smoke.

---

## 1. Executive summary — топ-5 находок

1. **`alert_dispatcher.py` SELECT по `alert_events` фильтрует только по `scan_id` без `created_at` → full-scan всех партиций.** Индекс по `scan_id` отсутствует. HIGH.
2. **`approve_draft_task(admin_override=True, approver_chat_id≠None)` обходит owner-check без явной проверки `is_admin_recipient`.** Caller (`ask.py`) делает её сам, но если другой caller забудет — bypass. Нет unit-теста на «caller контракт». HIGH.
3. **Snooze expire window `snoozed_until == cycle_ts` (edge equality) и pipeline-сценарий «snooze протух между сканами» не покрыты.** В pipeline-тесте есть только dashboard и enable_reco. MID.
4. **`handle_draft_callback` — TG callback с чужим chat_id (≠ owner, ≠ admin) → "Чужой черновик" footer.** В `test_e2e_ai_draft_to_mutation.py` нет сценария «alien-draft» через callback. Только на queue-уровне. HIGH.
5. **Property-based / fuzzing полностью отсутствует.** Нет ни одного `hypothesis.given`. Для evaluator-логики (26 тестов на 6 правил) пограничные/inverted-input проверены вручную, но fuzz-сценарии не разворачивались. MID.

**Verdict (см. §6): один-два целевых раунда.** Backend в целом готов, но 4-5 CRIT/HIGH gap'ов в логических сценариях стоит закрыть перед prod-нагрузкой.

---

## 2. Inventory тестов по доменам

### Observer / FSM (≈ 50 тестов)

| Файл | Тестов | Что покрыто |
|---|---|---|
| `tests/unit/test_observer_state_machine.py` | 13 | Все основные переходы decide(): normal↔warning_sent↔stop_sent + claimed/disabled terminal, fast-stop, deescalation, recovery. |
| `tests/unit/test_state_machine_token_persistence.py` | 11 | Token sохраняется при эскалации, генерируется новый при normal→warning/stop, обнуляется при recovery, NULL→new при missing fallback. |
| `tests/unit/test_match_offer_determinism.py` | 5 | Длиннейший матч + alphabetical tie-breaker (HIGH #16). |
| `tests/integration/test_observer_db.py` | 6 | Реальный pipeline на pg_engine: новый ad / spend_no_dep STOP / повтор без дубля / без оффера / scan_id propagation. |
| `tests/integration/test_observer_concurrent_fsm_write.py` | 4 | **WHERE-guard claimed/disabled (CRIT #5)** + warning→stop эскалация при concurrent state | open_token persistence через прямой UPDATE. |
| `tests/integration/test_observer_worker_loop.py` | 6 | Main loop (sleep, heartbeat, граф)  |
| `tests/integration/test_writers_reset.py` | 10 | reset_alert_state_after_disable/enable_succeeded — идемпотентность, защита от downgrade. |
| `tests/integration/test_scan_run_atomic.py` | 3 | **CTE atomicity (HIGH #9) — параллельные _begin_scan_run не дублируют id**. |

**Race condition coverage:** `test_observer_does_not_overwrite_claimed`, `test_observer_does_not_overwrite_disabled` + `test_warning_to_stop_persists_same_open_token` — но это только два корректных пути (`claimed`/`disabled`). Не покрыто: **concurrent UPDATE двух observer'ов на одно состояние** (FOR UPDATE / SKIP LOCKED не используется в `apply_fsm_transition`, полагаемся на ON CONFLICT WHERE).

**Не покрыто:**
- `_suppress_emit(snoozed)` в pipeline — нет E2E теста на snooze→скан→emit=False→ad_alert_state обновлено.
- Snooze expiration boundary (`snoozed_until == cycle_ts`).
- Reopen из disabled (через `reset_alert_state_after_enable_succeeded` есть, но нет: «observer видит активный ad в disabled → должен ли он эскалировать?»).
- FSM-input с `(warning,)+(stop,)` одновременно при `current=disabled` — игнорируется (есть), но нет проверки что метрики и так пишутся.

### task_queue / outbox / reconciler (≈ 40 тестов)

| Файл | Тестов | Покрытие |
|---|---|---|
| `tests/integration/test_tasks_queue_db.py` | 9 | CRUD: create/claim/mark_succeeded/mark_failed/requeue + idempotency dup + retry backoff + draft expiration. |
| `tests/integration/test_queue_mark_returns_bool.py` | 7 | **CRIT #2 — bool-return после Round 6**: mark_succeeded→True/False по WHERE status='running' guard. |
| `tests/integration/test_outbox_race_no_double_execution.py` | 2 | **CRIT #2 — zombie worker A** не downgrade'нет succeeded/failed после reconciler+worker B race. |
| `tests/integration/test_reconciler_no_double_bump.py` | 3 | **CRIT #3 — attempt_count + 1 ровно один раз** в каноническом `reconcile_stuck_running`. |
| `tests/integration/test_reconciler_worker_db.py` | 6 | Reconciler-worker env-обёртка (старше 30 мин running → retrying). |
| `tests/integration/test_toggle_worker_db.py` | 6 | execute_one_toggle_task: happy + grpc error + success=false + bad payload. |
| `tests/integration/test_meta_api_outbox_e2e.py` | 10 | **draft→approve→claim→execute** + RateLimited→requeue + TokenInvalid→failed + concurrent claim SKIP LOCKED. |
| `tests/integration/test_approve_draft_owner_acl.py` | 5 | **CRIT #6 owner ACL**: chat_id match, mismatch → False, NULL+admin_override→True, NULL без override→False. |
| `tests/integration/test_ai_drafts_e2e.py` | 4 | DRAFT → approve via approve_draft_task → claim → cancel (повторный = no-op). |

**Гонки реально проверены:**
- concurrent claim 5 параллельных worker'ов (test_meta_api_outbox_e2e:408) — один захват.
- zombie A + reconciler + worker B (test_outbox_race_no_double_execution.py) — succeeded не перетёрто.

**Не покрыто:**
- **Race: claim + mark_succeeded в одной транзакции (read-after-write)** — гарантирует ли engine.begin() видимость claim'а?
- **Reconciler видит stale `retrying` (а не `running`)** — bumps ли он attempt_count? Контракт `WHERE status='running'`.
- `requeue_for_retry` → backoff exact timestamps (есть Timed-тест? — не вижу).
- Conflict при `create_task` без idempotency — `idempotency_key` обязателен; нет теста на `idempotency_key=None`.
- `create_task` со `status != ('draft','pending')` — ValueError, не покрыт.

### Meta API / mutations / dispatch (≈ 90 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_meta_api_mutations.py` | 23 | Все 10 handlers + validation: pause/activate ad/campaign, set_adset_budget (daily/lifetime/both/negative/bool), duplicate, bulk_status_change. |
| `test_meta_api_create_campaign_full.py` | 17 | **Batch API + JSONPath refs + image_hash/video_id mutex + Adset budget mutex + targeting validation + subrequest fail propagation.** |
| `test_meta_api_custom_audience.py` | 15 | CUSTOM + LOOKALIKE + ratio validation + DELETE. |
| `test_meta_api_duplicate_campaign_atomic.py` | 6 | **Atomic batch (CRIT #1)**: rename через Batch API, failure → warning. |
| `test_meta_api_set_ad_creative.py` | 6 | Замена creative у Ad. |
| `test_meta_api_upload.py` + `test_meta_api_upload_url.py` | 23 | UploadVideo client-streaming, UploadImage unary, URL-mode без multipart, chunked 4MB, retry. |
| `test_meta_api_batch_helpers.py` | 25 | **Custom `_encode_value` оставляет JSONPath refs нетронутыми (CRIT #1)** + form encoding edge-cases. |
| `test_batch_helpers_jsonpath_refs.py` | 6 | JSONPath ref-конструктор + parse_batch_response. |
| `test_set_adset_budget_cap.py` | 6 | **HIGH #11 — cap $100k daily / $1M lifetime.** |
| `test_meta_api_dispatch.py` | 4 | Registry покрывает все MUTATION_KINDS + dispatch → handler. |
| `test_meta_api_adapters.py` | 7 | MetaApiAdRow → ScannedAdRow маппинг. |
| `test_meta_api_errors.py` | 9 | Классификация Graph error codes: 4 (permanent), 17 (rate), 190 (token). |
| `test_meta_api_queue.py` | 6 | create_mutation_task / create_draft_task / approve / cancel. |
| `test_meta_api_audit.py` | 5 | record_audit_log базовый. |
| `test_meta_api_schemas.py` | 4 | MetaMutationPayload from_dict / to_dict. |

**Coverage интеграции в worker** — `test_meta_api_outbox_e2e.py` mocked dispatch. Реальный gRPC к browser-agent не нужен (eager-init), маршрутизация ошибок проверена.

**Не покрыто:**
- **`bulk_status_change` с invalid object_id**: warning log + caller-side type guard упомянуты в docstring, но **нет теста на сам warning** — что лог реально пишется?
- **`create_campaign` с пустым `params={}`** → должна упасть на валидации до Batch — нет теста.
- **Сценарий «`act_` префикс через batch references»** (`{result=campaign:$.id}`) — закодирован в `test_batch_helpers_jsonpath_refs.py`, но end-to-end на executable batch не проверен.
- **CustomAudience с CSV-streaming `/users`** — в backlog (CLAUDE.md).
- **Race: два concurrent `claim_pending_task` для meta_api_mutation после reconciler-bump** — есть `test_concurrent_claim_skip_locked` (5 worker'ов), но без reconciler-zombie. Конкретно для meta_api worker'а не проверен.

### Telegram (≈ 60 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_telegram_renderer.py` | 9 | Render WARNING/STOP с inline-кнопками + token в callback. |
| `test_telegram_bot_handler.py` | 9 | /start, /help, /spy parsing, invite consume. |
| `test_telegram_handlers_creator.py` | 11 | Inline kb для creator-related events. |
| `test_telegram_settings_compute.py` | 18 | Bot username cache (Redis TTL 1h), deep link, poller_status. |
| `test_telegram_poller_meta_api.py` | 3 | Drafts list + dr_ok/dr_cancel callback dispatch. |
| `test_telegram_poller_e2e.py` (integration) | 4 | Real long-poll loop через respx-mock. |
| `test_telegram_send_via_respx.py` | 5 | Базовый send_message contract. |
| `test_telegram_alert_dispatcher.py` | 5 | Warning vs stop thread, idempotent skip, send_message error. |
| `test_alert_dispatcher_no_duplicate_send.py` | 2 | **Pre-claim защита от двойного TG (HIGH #8): параллельные dispatch'и → 1 sendMessage**. |
| `test_e2e_alert_dispatch_idempotent.py` | 3 | Pipeline → dispatcher: 2 scans = 1 TG message. |
| `test_e2e_ai_draft_to_mutation.py` | 3 | dr_ok callback → DRAFT → PENDING → worker → succeeded. |
| `test_api_settings_telegram.py` (integration) | 11 | PUT/DELETE bot_token, recipients, invites. |

**Не покрыто:**
- **`handle_draft_callback` со «чужого draft»** (chat_id≠owner и не admin) → должен вернуть "Чужой черновик" footer. Только в `test_approve_draft_owner_acl.py` на queue-уровне.
- **`snz:` (snooze) callback** — есть код в `core/telegram/handlers/alerts.py:91`, но тестов нет.
- **TG token rotation через crypto.rotate_encryption_key** — есть `test_telegram_settings_compute.py`, но без реальной ротации зашифрованных blob'ов.
- **`is_admin_recipient(chat_id=X)` где chat_id принадлежит recipient'у с `role='recipient'` (не owner) и `revoked_at IS NOT NULL`** — фильтры в SQL есть, но не проверены тесты на каждый комбо.

### Dashboard / History / Snapshots (≈ 110 тестов)

| Router | Тестов | Что покрыто хорошо | Что нет |
|---|---|---|---|
| `dashboard.py` (`/dashboard/ads,alerts,incidents`) | 13 | empty/filter/X-Total-Count/include_inactive/100 ads perf/22 validation. | Race INSERT alert_event vs SELECT (partial visibility). |
| `dashboard_stats.py` | 8 | observer_status Redis fail → unknown, fail-all gather, partial /batch. | scan_runs за > 7 дней — partition pruning. |
| `dashboard_timeseries.py` (/spend-history, /chart-data) | 11 | empty / 24h / 168h cap / bucket=hour|day / **partition pruning >24h** / active_ads DISTINCT. | `hours=0` boundary; `fb_ad_id=null` + `limit=10000`. |
| `dashboard_performance.py` | 8 | top campaigns/offer leaderboard/rule violations (jsonb_array_elements_text). | Race: top_campaigns ranking стабилен при concurrent metrics. |
| `dashboard_batch.py` | 5 | all keys, limits, empty, partial failure Redis down, perf. | Какие конкретные keys missing на partial failure? |
| `dashboard_incidents.py` | 6 | snoozed > NOW excluded, transitions_count batch-unnest no N+1, stage=warning/stop/all. | snoozed boundary (=NOW). |
| `dashboard_alerts.py` | 8 | from_iso/to_iso 422 / stage validation / partition WHERE. | Большое окно (>168h) — sanity. |
| `dashboard_ads_timeline.py` (`/ads/{fb_ad_id}/timeline`) | 10 | metrics+alerts+tasks мульти-source. | Empty fb_ad_id; 404 для несуществующего. |
| `history.py` | 18 | summary 30d default, max 90d → 422, jsonb_array_elements_text, LATERAL, partition WHERE timing test. | days=0; events filter combos. |
| `offers.py` (CRUD+rules+compare) | 16 | empty/include_inactive/compare metrics/days range/409 dup/422 lowercase/code immutable/soft delete. | Bulk update; concurrent PUT same offer. |
| `disable_tasks.py` / `enable_tasks.py` | 14 | status filter PENDING→[draft,pending]/POST create/retry/cancel/422. | Idempotency key uniqueness through POST; concurrent retry race. |
| `enable_recommendations.py` | 7 | empty/pending only/promoted/confirm happy/already promoted 409/not found 404. | Stale recommendation (созданная >7 дней назад). |
| `fake_deposits.py` | 8 | PUT/DELETE/list / **negative count 422**. | Concurrent PUT same fb_ad_id. |
| `auto_enable.py` | 8 | create/delete/list flag AdAutoEnableDisabled. | Idempotent flag re-create. |
| `ads_timeline.py` | 10 | timeline композит. | from_iso > to_iso explicit. |
| `observer.py` (`/observer/status,scan-runs,restart`) | 10 | status from Redis, scan-runs with partitioned WHERE, restart publish. | Subscriber-side acknowledgement (TODO в CLAUDE.md). |
| `health_details.py` | 5 | SCAN MATCH worker:heartbeat:* → ONLINE/OFFLINE. | Redis SCAN failure → graceful fallback? |
| `settings_observer.py` / `settings_vision.py` / `settings_telegram.py` | 25 | PUT/scan-now/auto-enable, Vision reconnect, token CRUD. | Vision reconnect gRPC failure → 5xx? |
| `tools.py` | 10 | uniquify happy, copies=0/MAX 422, no files, folder outside root 403, plan happy/invalid body. | Concurrent uniquify в одну папку. |
| `ai_analyze.py` | 8 | 503 no providers, cache hit, force_refresh bypass, rate limit 20/h, invalid block_type. | Redis недоступен → Cache miss path? |
| `postback.py` / `postback_security.py` | 12 | **compare_digest constant-time**, 401 wrong, 413 oversized, 422 empty, body на границе лимита. | x-postback-secret header missing (различить от wrong). |
| `health.py` | 5 | /healthz без БД, /readyz TTL 5s. | /readyz при недоступном Redis → 503? |
| `api_router_discovery.py` | 5 | auto-discovery пакета v1/. | — |

**Не покрыто (типичные corner cases):**

- **Empty fb_ad_id или невалидный UUID `campaign_id`** — есть для history (422), но не везде (auto_enable, fake_deposits).
- **`limit=0`** — для большинства endpoint'ов `ge=1`, но `tools/campaign-create/folders` без limit param.
- **Malformed JSON body** — большинство тестов шлёт правильный body, не malformed.
- **Concurrent requests на тот же ресурс** — нет нагрузочных тестов в integration.
- **Partition-key out of range** — `days=91` → 422 в history (есть), но `from_iso=2020-01-01` (за пределы retention 365d) — не проверено.
- **Stale data scenarios** — что если enable_recommendation создана > 7 дней назад? Cleanup в `cleanup_worker`, но не на endpoint-уровне.
- **`/dashboard/spend-history` без `fb_ad_id`** — limit 10000, но что если в БД есть 50 000 точек?

### AI Tools (≈ 50 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_ai_tools_registry.py` | 12 | GLOBAL_REGISTRY immutable, list_names, dup raise. |
| `test_ai_tools_meta.py` | 11 | All 5 meta-tools (set_adset_budget, pause, etc) через ToolContext. |
| `test_ai_tools_drafts.py` | 16 | All 4 draft tools: happy + validation (bad adset_id, both budgets, missing filter, draft collision, no engine). |
| `test_ai_ratelimit_fallback.py` | 4 | **HIGH #13 — Redis fail → in-memory secondary cap 5/60s, не fail-open**. |
| `test_request_bulk_pause_word_boundary.py` (integration) | 6 | **HIGH #14 — `~*` anchored word-boundary (CR не матчит ACRO)**. |
| `test_mcp_tool_adapter.py` | 6 | DRAFT_REQUIRED prefix, READ_ONLY pass-through, idempotency, real GLOBAL_REGISTRY adapts. |
| `test_mcp_context.py` | 6 | MCPContextManager, safe_dsn (без password в логах). |
| `test_mcp_call_tool.py` (integration) | 4 | Real call_tool через MCP server: READ_ONLY, DRAFT, unknown, rate limit. |
| `test_mcp_resources.py` (integration) | 6 | 4 MCP resources (offers/alerts/health/schema). |

**Не покрыто:**
- **TG callback `dr_ok` со СВОЕГО draft, но recipient revoked_at IS NOT NULL** — `is_admin_recipient` отфильтрует, но нет explicit-теста.
- **Owner-side ACL bypass attempt:** approve_draft_task с `approver_chat_id=X` и `admin_override=True` одновременно — какой путь выбирается? (см. code: admin_override=True переписывает strict check. Это потенциальная уязвимость: caller передал чужой chat_id + override=True → должно быть запрещено? Сейчас разрешено.)
- **DRAFT_REQUIRED tool с CREATIVE-level вместе** — RiskLevel смешан, но `mcp_tool_adapter` префикс ставит только DRAFT.

### AdSet.pro (≈ 50 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_adset_pro_client.py` (unit) | 16 | Bearer header, classify HTTP error (auth/rate/5xx/4xx), JSON-RPC envelope, stats args, extract_tool_result. |
| `test_adset_pro_schemas.py` | 7 | StatsQueryRequest/Response. |
| `test_adset_pro_ingest_dedup.py` (unit) | 5 | Dedup window mapping. |
| `test_adset_pro_client_http.py` (integration via respx) | 12 | health, 401, 404, 429 retry, 5xx retry, MCP tool fallback. |
| `test_adset_pro_ingest.py` (integration db) | 5 | FK resolve, dedup within window, no FK для unknown, distinct event_type. |
| `test_adset_pro_live.py` | 1 | smoke против real MCP. |
| `test_evaluator_external_deposits.py` | 3 | RuleContext.external_deposits защищает от STOP при наличии депозита. |

**Не покрыто:**
- **Bearer token rotation** — `ADSETPRO_MCP_KEY` читается из env при каждом запросе или один раз? Нет теста на «токен поменялся между запросами».
- **JSON-RPC ошибка (id mismatch, parse error)** — тест на корректность `_make_rpc_envelope` есть, но не на ответ с error: { code, message }.
- **Concurrent ingest того же click_id** — pre-INSERT SELECT + ON CONFLICT, но **гонка SELECT-INSERT-INSERT** между двумя процессами не покрыта (двухступенчатый дедуп упомянут в CLAUDE.md, но тест отсутствует).

### Digest / Enable-reco / Health watchdog (≈ 50 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_digest_scheduler_helpers.py` | 11 | is_in_send_window, redis-флаг 26h TTL. |
| `test_digest_catchup.py` | 6 | **MID #17 — catch-up до конца суток UTC**. |
| `test_digest_renderer.py` | 7 | HTML render. |
| `test_digest_builder.py` (integration) | 7 | Pure SQL aggregations: alerts by stage, tasks succeeded/failed, top ads, **active_ads_normal с last_seen_at**. |
| `test_digest_scheduler_loop.py` | 6 | Loop integration + no_tg_config silent + no_recipients flagged. |
| `test_e2e_digest_aggregation.py` | 3 | scan→toggle→digest показывает реальный disable. |
| `test_digest_active_ads_window.py` | 3 | **MID #20 — last_seen_at filter**. |
| `test_enable_reco_analyzer.py` (unit) | 14 | Should_recommend pure: snoozed skip, cooldown, spend after disable, etc. |
| `test_enable_reco_worker.py` (integration) | 6 | fetch_candidates + insert recommendation. |
| `test_e2e_enable_reco_loop.py` | 3 | full cycle: disable → wait cooldown → recommend → user confirm → enable task → toggle. |
| `test_health_watchdog.py` (unit) | 13 | dedup ключ, parser of heartbeat keys. |
| `test_health_watchdog_redis.py` (integration) | 9 | observer:runtime freshness check, SCAN MATCH worker:heartbeat:*. |

**Не покрыто:**
- **Health watchdog: при первом подключении TG-токена пропускает «упущенные» алерты** — указано в CLAUDE.md, но нет соответствующего теста.
- **Enable-reco: повторная рекомендация для того же ad через 6h Redis TTL** — есть skip-тест, но нет «после TTL → можем снова».
- **Digest: окно перекрытия с DST/timezone changes** — есть тесты для UTC, нет для произвольных зон.

### Creator / Ad Library (≈ 50 тестов)

| Файл | Тестов | Что |
|---|---|---|
| `test_creator_worker_routing.py` | 13 | Routing of plan_run errors → mark_failed/requeue. |
| `test_creator_worker_lifecycle.py` (integration) | 3 | claim → run → mark_succeeded. |
| `test_e2e_creator_full_lifecycle.py` | 3 | Полный pipeline. |
| `test_creator_recorder_pubsub.py` | 7 | Subscription на record_start/stop. |
| `test_ad_library_classifier.py` | 10 | vertical + relevance. |
| `test_ad_library_enricher.py` | 5 | hook/cta/tone. |
| `test_ad_library_media.py` | 5 | downloader. |
| `test_ad_library_tier_ranker.py` | 8 | S/A/B/C ranking. |
| `test_ad_library_spy_handler.py` | 8 | Parse `/spy <slot> <country>`. |
| `test_ad_library_pipeline_e2e.py` | 4 | full pipeline. |

### Прочее (≈ 50 тестов)

- `test_api_cors_validation.py` (4) — **HIGH #12: `"*"` в frontend_origin → RuntimeError на старте.**
- `test_api_health_details.py` (5) — health-check.
- `test_pubsub_listener.py` (5) — RedisPubSubListener helper.
- `test_worker_subscribers.py` (4) — Worker subscribers на Redis-каналы (restart/scan-now/cabinet_day).
- `test_cleanup_worker_db.py` (2) — partition DROP + retention DELETE.
- `test_cleanup_retention.py` (9) — pure helpers.
- `test_creative_uniquify.py` (3) — image watermark.
- `test_campaign_name.py` (3) — naming pure.
- `test_humanizer.py` (4) — pure utility.

---

## 3. Smoke vs логика — критичные домены

### 3.1 FSM transitions (state_machine.py)

**Покрыто:** все 9 перечисленных переходов + token contract + claimed/disabled terminal.

**Не покрыто (gap'ы):**

| Сценарий | Тестируется? | Где должно быть |
|---|---|---|
| `claimed → claimed` (STOP сохраняется) | ✅ | unit (есть). |
| `claimed → normal` (recovery) | ❌ | FSM не позволяет — это обязанность `reset_after_enable_succeeded`. Документация ОК, нет регрессии. |
| `disabled → warning_sent` (re-active) | ❌ | Reopen логика. Нет такой логики в FSM. Через `reset_after_enable_succeeded` идём в normal сначала. ОК. |
| `snoozed_until == cycle_ts` (boundary) | ❌ | Pipeline `> cycle_ts` — strict greater, при равенстве не подавляет. **Stale тест.** |
| Snooze expiry между двумя сканами | ❌ | Нет E2E теста: snooze на 2h → ad в warning_sent suppressed → cycle_ts > snoozed_until → emit. |
| Concurrent decide() — нет, pure | n/a | pure-функция, не нужен. |
| `current_open_token=valid_uuid + warning_sent → warning_sent + new warning codes` | ❌ | warning_codes изменились (например cpc_warn → freq_warn) — что происходит с alert_events? Не проверено. |

### 3.2 Outbox race conditions (`core/tasks/queue.py`)

**Покрыто:**
- bool returns после Round 6 (`test_queue_mark_returns_bool.py` — 7 тестов).
- Zombie A после reconciler + worker B (`test_outbox_race_no_double_execution.py`).
- attempt_count bump только один раз (`test_reconciler_no_double_bump.py`).

**Не покрыто:**
- **`claim → mark_succeeded` в одной asyncio.Task vs `claim` в другой**: Postgres SKIP LOCKED это закрывает, но **read-after-write через `RETURNING` consistency** не тестировался отдельно.
- **`requeue_for_retry` next_retry_at точность**: exponential backoff (30/60/120/240/300 sec). Контракт `_calc_next_retry` — pure-функция, но **нет unit-теста на её выходы**.
- **Concurrent `mark_failed` + `requeue_for_retry`** — два worker'а вызывают на одну задачу: `WHERE status='running'` сужает оба, ровно один сработает, но **нет теста**.
- **`max_attempts` boundary**: `attempt_count = max_attempts - 1` → `requeue_for_retry` → `mark_failed`, не `retrying`. Покрыто в test_meta_api, но не в общих tasks_queue_db.

### 3.3 Meta API mutations dispatch (`core/meta_api/mutations/__init__.py`)

**Покрыто:**
- Registry coverage (`test_meta_api_dispatch:test_registry_covers_all_mutation_kinds`).
- Each handler declares its kind.
- Known kind → правильный handler.
- Unknown kind → NotImplementedError.

**Не покрыто:**
- **Webhook 429 → exponential backoff**: meta_api worker сам classifier'ит `RateLimitedError` → `requeue_task` → `_calc_next_retry(attempt+1)`. Покрыто в `test_meta_api_outbox_e2e:test_rate_limited_error_requeues_task`, но **только проверяется `status='retrying'`, не точный timestamp backoff'а**.
- **TemporaryError vs RateLimitedError** — оба идут в requeue. **Нет теста на разницу логирования**.
- **dispatch_mutation бросает Exception (не классифицированный)** — попадает в `except Exception` → requeue (защитный). Тест есть только на known classes (RateLimited/TokenInvalid).

### 3.4 alert_dispatcher pre-claim

**Покрыто:**
- `test_alert_dispatcher_no_duplicate_send.py:test_parallel_dispatch_sends_message_once` — два параллельных dispatch'а → ровно 1 sendMessage.
- `test_failed_send_releases_claim` — send упал → DELETE pre-claim.
- `test_e2e_alert_dispatch_idempotent.py` — pipeline → dispatcher: 2 scans = 1 TG.

**Race окно SELECT-INSERT действительно закрыто** через `INSERT ... ON CONFLICT DO NOTHING RETURNING` — pre-claim паттерн правильный.

**Не покрыто:**
- **Pre-claim INSERT успешен, send_message успешен, но UPDATE message_id падает (БД временно недоступна)** — sentinel остаётся `message_id=0`. Это не нарушает идемпотентность, но **сейчас тест не проверяет, что следующий dispatch не пошлёт повторно при `message_id=0`**.
- **Sentinel `message_id=0` дедуп**: тест `test_double_dispatch_same_scan_id_skipped_via_message_ref` проверяет, что повторный dispatch скипает (через `incident_key`), но не специфически через `message_id=0`.

### 3.5 Partitioned queries — все ли фильтруют по partition key?

Проверены 18 SELECT'ов из `apps/api/routers/v1/` и `core/`:

| Файл | Partition table | Partition key filter? |
|---|---|---|
| `dashboard.py:223` | alert_events | ✅ created_at BETWEEN |
| `dashboard_stats.py:112,121` | scan_runs | ✅ started_at >= week_ago/today_start |
| `dashboard_stats.py:283` | alert_events | ✅ created_at >= day_ago |
| `dashboard_timeseries.py:91,152` | ad_metrics | ✅ cycle_ts BETWEEN |
| `dashboard_performance.py:193,127,139` | alert_events, ad_metrics | ✅ created_at, cycle_ts |
| `history.py` (все 6 SELECT'ов) | alert_events, ad_metrics | ✅ BETWEEN |
| `offers.py:127,156` (compare) | ad_metrics, alert_events | ✅ cycle_ts/created_at >= period_start |
| `dashboard/snapshot.py:226` (LATERAL) | ad_metrics | ✅ cycle_ts >= NOW - lookback |
| `digest_builder.py:73,181,213` | alert_events, ad_metrics | ✅ BETWEEN window_start/end |
| `ai_assistant/tools/ops/get_recent_alerts.py:59` | alert_events | ✅ created_at >= make_interval(hrs => :hrs) |
| `enable_recommendation_worker/main.py:115` | ad_metrics | ✅ cycle_ts > :since |
| `adset_pro/queries.py:45,82` | adsetpro_postback_events | ✅ received_at >= cutoff |
| `adset_pro/ingest.py:78` | adsetpro_postback_events | ✅ received_at BETWEEN |
| **`core/telegram/alert_dispatcher.py:62`** | **alert_events** | ❌ **WHERE e.scan_id = :sid** только |
| `meta_api/audit.py:106` | meta_api_audit_log | ⚠️ COUNT(*) — нет filter (есть retention 30d, но full-scan активных партиций) |
| `mcp_server/resources.py:163` | alert_events | ✅ created_at >= NOW - interval |

**FINDING:** `core/telegram/alert_dispatcher.py:50-73` — `WHERE e.scan_id = :sid` без `created_at`. Индекса `scan_id` на `alert_events` нет (`alert_event.py:55-66` показывает только `ix_alert_events_ad_created`, `ix_alert_events_stage`, `ix_alert_events_state`, `ix_alert_events_token`). Этот запрос вызывается **каждый раз после observer scan** — full-scan всех партиций (~365 дней). Это HIGH performance bug + потенциально HIGH данных-corrupt (медленный SELECT блокирует выполнение alert_dispatcher → TG-сообщения копятся).

**FINDING:** `core/meta_api/audit.py:106` — `SELECT COUNT(*) FROM meta_api_audit_log` без partition filter. Используется в reconciler? Проверить.

### 3.6 MCP server adapter risk-level + ACL

**Покрыто:**
- `test_mcp_tool_adapter.py` — DRAFT_REQUIRED prefix добавляется, READ_ONLY/CREATIVE — нет.
- `test_global_registry_all_tools_adapt_without_errors` — все 15 tools адаптируются.
- `test_mcp_call_tool.py` — call_tool на DRAFT_REQUIRED создаёт строку в task_queue.

**Не покрыто:**
- **ACL filtering в MCP**: текущая реализация **не фильтрует** tools по client_id MCP. Любой подключённый MCP-клиент имеет доступ ко всем 15 tools (включая DRAFT_REQUIRED). Это by design — потому что approve всё равно требует TG. Но **нет explicit-теста, что MCP-only клиент не может обойти TG-approve** (например через прямой call_tool на каждое DRAFT-tool).
- **MCP client_key persistence в ToolContext**: `client_key="ai:mcp:claude-desktop"` хардкод? Если так, **все MCP-клиенты разделяют rate-limit**. Это сейчас работает (1 пользователь), но **нет теста на multi-client**.

### 3.7 AI tools drafts — owner ACL (CRIT #6)

**Покрыто:**
- `test_approve_draft_owner_acl.py` — 5 тестов на queue-уровне.
- `test_e2e_ai_draft_to_mutation.py:test_double_approve_callback_is_noop` — повторный approve no-op.

**Не покрыто:**
- **Чужой draft через TG callback**: handler в `core/telegram/handlers/ask.py:226-249` имеет ветку «Чужой черновик» — нет теста.
- **`admin_override=True` без `is_admin_recipient` проверки caller'ом**: queue.py:178 разрешает любому caller'у с `admin_override=True` подтвердить ЛЮБОЙ draft, включая не свой. Это by design: caller обязан проверить через `is_admin_recipient` сам. Но **нет теста, что caller это делает**.

### 3.8 AdSet.pro MCP client — JSON-RPC errors + Bearer rotation

**Покрыто (test_adset_pro_client_http.py):**
- 401 → AuthError без retry.
- 404 → NotFound.
- 429 → retry then raise.
- 5xx → retry then succeed.
- network error → false (health).

**Не покрыто:**
- **JSON-RPC error response** (`{"jsonrpc":"2.0","error":{"code":-32601,"message":"Method not found"},"id":1}`) — есть тест на `extract_tool_result_falls_back_to_text_json`, но не на error-payload.
- **Bearer token rotation** — клиент создаётся с токеном из env. **Нет теста на «токен обновился между двумя запросами»**. Сейчас приходится перезапускать процесс — это не bug, но не запротестировано.

---

## 4. Corner cases per-endpoint

| Endpoint | Empty DB | Boundary | Malformed | Concurrent | Auth | Partition out-of-range | Stale data |
|---|---|---|---|---|---|---|---|
| `GET /offers` | ✅ | n/a | n/a | ❌ | n/a | n/a | n/a |
| `GET /offers/compare?days=N` | ❌ | ✅ (1≤N≤90) | n/a | ❌ | n/a | ❌ days>365 retention | ❌ |
| `POST /offers` | n/a | ✅ (lowercase code 422) | ❌ | ❌ (PUT same code) | n/a | n/a | n/a |
| `PUT /offers/{id}` | n/a | ✅ (immutable code) | ❌ | ❌ | n/a | n/a | n/a |
| `DELETE /offers/{id}` | n/a | ✅ (already inactive 404) | n/a | ❌ | n/a | n/a | n/a |
| `GET/PUT /offers/{id}/rules` | ✅ | ✅ (negative→422) | ❌ | ❌ | n/a | n/a | n/a |
| `GET /dashboard/ads` | ✅ | ✅ (limit cap) | ❌ | ❌ | n/a | n/a | ❌ |
| `GET /dashboard/alerts` | n/a | ✅ from_iso>to_iso 422 | n/a | ❌ | n/a | ❌ from_iso=2020-01-01 | n/a |
| `GET /dashboard/incidents` | n/a | ✅ snoozed_until>NOW | ❌ | ❌ | n/a | n/a | ❌ |
| `GET /dashboard/stats` | n/a | n/a | n/a | n/a | n/a | n/a | ✅ Redis fail→unknown |
| `GET /dashboard/batch` | ✅ | ✅ limits | n/a | n/a | n/a | n/a | ✅ partial-failure |
| `GET /dashboard/spend-history` | n/a | ✅ 24h/168h cap | n/a | ❌ | n/a | n/a | n/a |
| `GET /dashboard/chart-data` | n/a | ✅ 24h boundary, partition pruning | n/a | ❌ | n/a | ✅ | n/a |
| `GET /dashboard/performance` | n/a | ✅ | n/a | ❌ asyncio.gather fail | n/a | n/a | n/a |
| `GET /dashboard/disable-tasks` | ✅ | ✅ status map | ❌ | ❌ retry race | n/a | n/a | n/a |
| `POST /dashboard/disable-tasks/{id}/retry` | n/a | ✅ 409 active | ❌ | ❌ concurrent retry | n/a | n/a | n/a |
| `GET /dashboard/enable-recommendations` | ✅ | ✅ pending/promoted | ❌ | ❌ | n/a | n/a | ❌ >7d old |
| `POST /enable-recommendations/{id}/enable` | n/a | ✅ 409 already promoted | ❌ | ❌ | n/a | n/a | ❌ |
| `GET/PUT /fake-deposits` | n/a | ✅ negative→422 | ❌ | ❌ same fb_ad_id | n/a | n/a | n/a |
| `GET /history/*` (6 endpoint'ов) | ✅ | ✅ days>90 422 | ❌ | ❌ | n/a | ❌ from_iso<retention | n/a |
| `GET /ads/{fb_ad_id}/timeline` | n/a | ❌ | ❌ | ❌ | n/a | n/a | n/a |
| `POST /tools/creative-uniquify` | n/a | ✅ copies=0/MAX | ❌ | ❌ same offer_name | n/a | n/a | n/a |
| `POST /tools/creative-uniquify/open-folder` | n/a | ✅ 403 outside root | ❌ | n/a | n/a | n/a | n/a |
| `POST /tools/campaign-create/plan` | n/a | ✅ invalid body | ❌ | n/a | n/a | n/a | n/a |
| `POST /ai/analyze` | n/a | ✅ rate limit | ✅ invalid block_type | ❌ | ✅ 503 | n/a | n/a |
| `POST /api/v1/postback/adsetpro` | n/a | ✅ 100KB→413, empty→422, border | n/a | ❌ same click_id | ✅ 401 wrong, **MISSING header undocumented** | n/a | n/a |
| `GET /observer/status` | n/a | n/a | n/a | n/a | n/a | n/a | ✅ |
| `POST /observer/restart` | n/a | n/a | n/a | n/a | n/a | n/a | ❌ subscriber no-op |
| `GET /health/details` | n/a | n/a | n/a | n/a | n/a | n/a | ✅ |
| `GET /settings/observer` | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `PUT /settings/telegram/token` | n/a | n/a | ❌ invalid token format | n/a | n/a | n/a | n/a |
| `POST /settings/telegram/recipients/invite` | n/a | n/a | n/a | n/a | n/a | n/a | ✅ expires_at |

**Малопокрытые corner case patterns:**

1. **Concurrent requests** — практически отсутствует во всём API layer. Только observer concurrent FSM write + meta_api claim race.
2. **Malformed JSON body** — нет explicit-тестов. FastAPI/Pydantic поймает, но контракт ответа (422 vs 400) не зафиксирован.
3. **Authorization edges**:
   - `POST /api/v1/postback/adsetpro` без header `X-Postback-Secret` → 401 vs 422?
   - `POST /ai/analyze` 429 при превышении лимита — есть, но **per-IP лимит**. Что если IP `0.0.0.0` (no client)?
4. **Partition out-of-range** — нет теста с `from_iso=2020-01-01` (за пределами retention).

---

## 5. Логика разделения тестов + Test-data quality

### Слишком общие smoke тесты — мало

В целом, тесты содержательные. Но несколько слабых:

- `test_api_health.py:test_healthz_returns_200` — просто status_code, без проверки content.
- `test_api_router_discovery.py:test_all_routers_register` — проверяет регистрацию, но не функциональность.
- `test_creator_recorder_pubsub.py:test_subscribe_basic` — subscribe API, без проверки парсинга.

### Дублирование между unit и integration

Найдены два случая:

- **`mark_succeeded/mark_failed` bool-return**: `test_queue_mark_returns_bool.py` (integration) + явные проверки в `test_outbox_race_no_double_execution.py`. Это не дубль — разные сценарии.
- **`approve_draft_task` логика**: `test_approve_draft_owner_acl.py` (5) + `test_e2e_ai_draft_to_mutation.py` (3). Не дубль — queue-level vs E2E через callback.

Сейчас разделение чёткое.

### Missing unit-тестов для pure-функций

| Pure function | Покрытие |
|---|---|
| `core.dashboard.snapshot._build_metrics_dict` | ✅ test_dashboard_snapshot.py |
| `core.dashboard.snapshot._build_sql` | ✅ |
| `core.dashboard.snapshot._build_row_dict` | ✅ |
| `core.observer.state_machine.decide` | ✅ |
| `core.rules.evaluator.evaluate_stop_rules` | ✅ (26 тестов) |
| `core.observer.queries.match_offer_for_ad` | ✅ test_match_offer_determinism.py |
| `core.tasks.queue._calc_next_retry` | ❌ **нет** |
| `core.telegram.digest_builder._top_ads_and_total_spend` | ✅ |
| `core.adset_pro.client._make_rpc_envelope` | ✅ |
| `core.adset_pro.client._classify_http_error` | ✅ |
| `apps.api.utils.status_mapper.to_frontend_task_status` | ✅ test_status_mapper.py |
| `apps.api.utils.partition.default_window` | ❌ **нет** (но используется в каждом router'е) |
| `core.observer.writers._serialize_metrics` (Decimal→str) | ❌ |

### Integration в unit-папке — нет

Проверил grep — unit-папка чистая от pg_engine.

### Test-data quality

**Реальные fixtures с invalid data:**

- ✅ Negative thresholds (offers, fake_deposits).
- ✅ Lowercase code (offers POST).
- ✅ Bool input в `set_adset_budget` отвергается.
- ✅ Both budgets (daily + lifetime) → 422.
- ✅ Bad adset_id ("abc" вместо numeric).
- ❌ **Нет тестов на NaN-spend, отрицательный spend**, infinite-spend.
- ❌ **Нет тестов на expired timestamps в payload** (e.g. AdSet.pro postback с `received_at = 2020`).
- ❌ **Нет тестов на injection в payload JSONB** (e.g. SQL-injection в `ad_name` через INSERT-time escape — SQLAlchemy защищает, но contract-test отсутствует).
- ❌ Property-based/Hypothesis полностью отсутствует.

---

## 6. Recommended new tests

### CRITICAL — реальные дыры в логике, могут вызвать данных corrupt или security issue

1. **[CRITICAL] `core/telegram/alert_dispatcher.py` SELECT без partition filter.**
   - Файл: `tests/integration/test_alert_dispatcher_partition_pruning.py` (новый).
   - Сценарий: вставить 100 alert_events за 30 дней (5 партиций), вызвать `dispatch_pending_alerts(scan_id=N)`, измерить duration. Сравнить с partitioned-friendly запросом (`WHERE created_at >= NOW() - INTERVAL '1 day' AND scan_id = N`). Сейчас разница может быть в 100×.
   - Также: добавить explain тест что pg использует `Seq Scan` или `Index Scan` — выявить full-scan.

2. **[CRITICAL] `approve_draft_task(admin_override=True, approver_chat_id=X)` каждому позволяет.**
   - Файл: `tests/integration/test_approve_draft_owner_acl.py` (добавить тест).
   - Сценарий: создать draft с `created_by_chat_id=111`. Вызвать `approve_draft_task(approver_chat_id=222, admin_override=True)` БЕЗ предварительной проверки `is_admin_recipient`. Должен вернуть True (по текущему коду). Это контракт: caller обязан проверить is_admin. **Тест должен фиксировать этот контракт** и блокировать regression: если в будущем кто-то добавит `is_admin_recipient` проверку внутри queue.py, контракт изменится. Альтернатива — переписать queue.py так, чтобы внутри было обязательно `is_admin_recipient` (но это нарушит существующие unit-тесты).

3. **[CRITICAL] `handle_draft_callback` с чужого chat_id (≠ owner, ≠ admin) → "Чужой черновик" footer.**
   - Файл: `tests/integration/test_e2e_ai_draft_to_mutation.py` (добавить тест).
   - Сценарий: создать draft `created_by_chat_id=111`. Вызвать `handle_draft_callback(chat_id=222, username="mallory")` (где 222 — не owner). Должен вернуть ack "Этот черновик принадлежит другому пользователю" + footer "🔒 Чужой черновик", БЕЗ перевода draft → pending.

4. **[CRITICAL] Concurrent `mark_failed` + `requeue_for_retry` на одну задачу.**
   - Файл: `tests/integration/test_outbox_race_no_double_execution.py` (добавить).
   - Сценарий: claim task. Параллельно asyncio.gather: `mark_failed(error="A")` + `requeue_for_retry(error="B")`. Ровно один должен вернуть True. Финальное state детерминировано: либо `failed`, либо `retrying`. Не оба.

5. **[CRITICAL] meta_api worker: `dispatch_mutation` бросает `Exception(generic)` → requeue.**
   - Файл: `tests/integration/test_meta_api_outbox_e2e.py` (добавить).
   - Сценарий: monkeypatch `dispatch_mutation` чтобы бросать `RuntimeError("unexpected")`. `process_one_task` → `status='retrying'`, `attempt_count=1`, последующая попытка снова падает → eventually `failed` после max_attempts. Защитная сетка `except Exception` в worker.

### HIGH — логически важный сценарий не проверен

6. **[HIGH] Snooze expire boundary (`snoozed_until == cycle_ts`).**
   - Файл: `tests/integration/test_observer_concurrent_fsm_write.py` (добавить).
   - Сценарий: установить `snoozed_until = NOW()`. Вызвать `process_scan_rows(cycle_ts=NOW())`. Pipeline должен **НЕ** suppress'ить (по коду `current.snoozed_until > cycle_ts` — equality не подавляет). Должно произойти emit.

7. **[HIGH] Snooze expired between two scans.**
   - Файл: новый тест в `test_observer_db.py`.
   - Сценарий: scan #1 ставит warning_sent. UPDATE ad_alert_state SET snoozed_until = NOW() + 1s. Сразу scan #2 (cycle_ts = NOW(), но snoozed_until > cycle_ts) → no emit. asyncio.sleep(2). Scan #3 (cycle_ts > snoozed_until) → emit_alert=True для warning (повторный, если правила сохранились).

8. **[HIGH] `core/adset_pro/ingest` concurrent ingest с тем же click_id.**
   - Файл: `tests/integration/test_adset_pro_ingest_concurrent.py` (новый).
   - Сценарий: 5 параллельных `ingest_postback` с одним click_id+event_type+received_at в одном dedup-window. Ровно один inserted=True, остальные inserted=False (через ON CONFLICT). SELECT COUNT(*) в БД = 1.

9. **[HIGH] `_calc_next_retry` exponential backoff exact values.**
   - Файл: `tests/unit/test_tasks_queue_helpers.py` (новый).
   - Сценарии: attempt=0 → +30s, attempt=1 → +60s, attempt=2 → +120s, attempt=3 → +240s, attempt=4 → +300s (cap), attempt=10 → +300s (cap).

10. **[HIGH] `is_admin_recipient` revoked_at edge cases.**
    - Файл: `tests/integration/test_meta_api_queue.py` (расширить).
    - Сценарии: chat_id с role='owner' active → True. role='owner' revoked → False. role='recipient' active → False. chat_id отсутствует → False.

11. **[HIGH] `dispatch_pending_alerts` send_message success без message_id (sentinel 0 не обновлён).**
    - Файл: `tests/integration/test_alert_dispatcher_no_duplicate_send.py` (добавить).
    - Сценарий: respx ответ `{"ok": true, "result": {"message_id": 0}}`. Pre-claim INSERT прошёл. UPDATE message_id=0 (sentinel). Повторный dispatch для того же event → skipped через `ON CONFLICT DO NOTHING`. Проверить что повторно не послался.

12. **[HIGH] alert_events JSON injection в `ad_name` через INSERT.**
    - Файл: `tests/unit/test_alert_dispatcher_render.py` (расширить).
    - Сценарий: ad_name = `<script>alert(1)</script>` или `"; DROP TABLE fb_ads; --`. SQLAlchemy parameterized → защищено, но **render_alert_text должен HTML-escape**. Проверить что в TG payload нет raw `<script>`.

13. **[HIGH] `set_adset_budget` cap edge case: ровно $100k daily, $100k+1 cent rejected.**
    - Файл: `tests/unit/test_set_adset_budget_cap.py` (добавить).
    - Сценарии: daily_budget=10_000_000 cents → OK. daily_budget=10_000_001 → ValueError. lifetime_budget=100_000_000 → OK. lifetime_budget=100_000_001 → ValueError.

14. **[HIGH] `BodySizeLimitMiddleware` boundary value (точно лимит).**
    - Файл: `tests/integration/test_api_postback_security.py` уже покрывает (`test_postback_accepts_body_at_limit`). **OK**.

15. **[HIGH] `core/meta_api/audit.py` audit log с записью при partition out-of-range.**
    - Файл: новый тест.
    - Сценарий: вставить через `record_audit_log` с фейковым `created_at` за пределами текущих партиций (далёкое прошлое). Должен либо успешно записать (если default partition есть), либо graceful fail с log warning (по docstring).

### MID — полезный, но не блокирующий

16. **[MID] `_serialize_metrics` Decimal→str и datetime→isoformat.**
    - Файл: `tests/unit/test_observer_writers_serializer.py` (новый).
    - Сценарии: Decimal('123.45') → "123.45", None → None, datetime(2026,1,1) → "2026-01-01T00:00:00+00:00", int 42 → 42.

17. **[MID] `default_window(hours=N)` граничные.**
    - Файл: `tests/unit/test_api_utils.py` (новый).
    - Сценарии: hours=0 → (NOW(), NOW()). hours=168 → точно 7 дней. hours=-1 → должно бросить или возвращать пустой.

18. **[MID] Property-based: evaluate_stop_rules с random valid metrics.**
    - Файл: `tests/unit/test_evaluator_property.py` (новый, с Hypothesis).
    - Сценарий: `@given(spend=floats(0, 1000), leads=integers(0, 1000), ...)` — проверить инварианты: `len(matched_codes) <= 6`, `stage in {None, 'warning', 'stop'}`, `stop_codes ∩ warning_codes = ∅`, etc.

19. **[MID] `_count_active_ads_normal` race с UPDATE last_seen_at.**
    - Файл: `tests/integration/test_digest_active_ads_window.py` (добавить).
    - Сценарий: ad с last_seen_at = NOW - 6 days 23h 59min (внутри 7d). Параллельно UPDATE на NOW - 7d 1min (вышел из окна). Digest должен видеть либо 1, либо 0 — детерминированно по transaction isolation.

20. **[MID] `ChatSession._RateLimiter` thread-safety.**
    - Файл: `tests/unit/test_chat_session_ratelimit.py` (новый).
    - Сценарий: 10 параллельных `limiter.hit(client_ip)`. Лимит = 5. Должно пройти ровно 5, остальные False.

21. **[MID] `transitions_count` batch-unnest с пустым массивом.**
    - Файл: `tests/unit/test_dashboard_snapshot.py` (расширить).
    - Сценарий: incidents запрос вернул rows=[], никаких unnest вызовов, transitions_by_id = {}. Проверить что не падает на UNNEST(CAST([] AS uuid[])).

22. **[MID] AdSet.pro JSON-RPC error response.**
    - Файл: `tests/integration/test_adset_pro_client_http.py` (расширить).
    - Сценарий: respx возвращает `{"jsonrpc":"2.0","error":{"code":-32601,"message":"Method not found"},"id":1}`. Должен бросить специфичную ошибку (Permanent? NotFound?), а не молча вернуть `{}`.

23. **[MID] MCP server мульти-клиент: разные client_key — разные rate limits.**
    - Файл: `tests/integration/test_mcp_call_tool.py` (добавить).
    - Сценарий: ToolContext A (client_key="ai:mcp:A") сжигает 30 вызовов. ToolContext B (client_key="ai:mcp:B") вызывает — должен пройти.

24. **[MID] `enable_recommendation` >7 days old.**
    - Файл: `tests/integration/test_api_enable_recommendations.py` (добавить).
    - Сценарий: создать reco с created_at = NOW - 8 days. GET /dashboard/enable-recommendations — должна ли она появляться? (Сейчас нет cleanup → возвращается. Это feature gap, нужно зафиксировать тестом.)

25. **[MID] Cleanup worker — DROP старой партиции, активные данные в свежей.**
    - Файл: `tests/integration/test_cleanup_worker_db.py` (расширить).
    - Сценарий: создать partition за май 2024, заполнить 100 строк. NOW = 2026-05-28, retention=365d. Cleanup → DROP partition. Проверить что строки за май 2026 не затронуты.

---

## 7. Health verdict

**Backend ready-for-prod: с оговорками. Один-два целевых раунда тестов.**

**Положительные сигналы:**
- 936 тестов — большой объём, хорошая концентрация на race conditions (15+ тестов на гонки).
- Все 6 раундов аудита (CRIT/HIGH/MID) закрыты регрессионными тестами.
- Pure-функции отделены от I/O; integration не утечает в unit.
- E2E цепочки покрыты (observer→disable, AI draft→worker, scan→TG).
- Все 17 v1-роутеров имеют integration-тесты.
- Все 10 meta_api mutations покрыты unit + integration.

**Что блокирует prod:**
1. **alert_dispatcher full-scan по alert_events** — может вызвать degradation при росте партиций (через месяц-два после старта). Critical fix + регрессионный тест.
2. **Чужой draft через TG callback** не закрыт регрессом. CRIT #6 решён на queue-уровне, но handler-level dispatch без тестов.

**Что желательно дополнить:**
- Tests #1-5 (CRITICAL).
- Tests #6-11 (HIGH).

**Что точно не блокирует, но "good to have":**
- Property-based для evaluator (#18).
- Multi-client MCP (#23).
- Edge cases на partition out-of-range (#15, #25).

**Оценка раундов до production-ready:**
- **0.5 раунда** на закрытие 5 CRITICAL — ~3-5 тестов, ~half day работы.
- **0.5 раунда** на 6 HIGH — ~6 тестов, ~half day.
- ⇒ **1 раунд** до запуска под нагрузкой.

**Не блокирует прод, но улучшит maintenance:**
- 10 MID — ~1-1.5 раунда дополнительно.

---

## Приложение A: Inventory unit + integration files

```
tests/unit/         54 файла, 498 тестов
tests/integration/  73 файла, 434 теста
Итого:              127 файлов, 932 теста (плюс ~4 e2e ad-library, итого 936)
```

Топ-10 unit-файлов по числу тестов:
1. test_evaluator.py — 26 тестов (rules engine)
2. test_meta_api_batch_helpers.py — 25
3. test_meta_api_mutations.py — 23
4. test_telegram_settings_compute.py — 18
5. test_meta_api_create_campaign_full.py — 17
6. test_meta_api_upload.py — 16
7. test_ai_tools_drafts.py — 16
8. test_adset_pro_client.py — 16
9. test_status_mapper.py — 15
10. test_meta_api_custom_audience.py — 15

Топ-10 integration-файлов:
1. test_api_history.py — 18 тестов
2. test_api_offers.py — 16
3. test_api_dashboard_ads.py — 13
4. test_adset_pro_client_http.py — 12
5. test_api_settings_telegram.py — 11
6. test_writers_reset.py — 10
7. test_meta_api_outbox_e2e.py — 10
8. test_api_tools.py — 10
9. test_api_observer.py — 10
10. test_api_disable_tasks.py — 10
