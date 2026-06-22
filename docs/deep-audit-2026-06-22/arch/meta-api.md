# Подсистема Meta API + мутации + outbox-исполнитель

Карта архитектуры на 2026-06-22. Read-only разбор логики и связей (не построчный пересказ).

## Назначение

Подсистема исполняет ВСЕ записи в Facebook через Marketing API: авто-стоп убыточной
рекламы (`pause_ad`), ручную паузу/включение, изменение бюджета, клонирование и создание
кампаний, bulk-операции. Это latency-tolerant канал (лаг 5-15 мин приемлем для всего, кроме
авто-стопа). Запросы к Graph API физически НЕ идут через httpx — они исполняются
`page.evaluate(fetch)` ВНУТРИ Vision-сессии (через gRPC к browser-agent), чтобы Meta видела
правильные cookies/fingerprint.

Это money-критичный контур: каждая мутация либо тратит бюджет (activate/create/budget), либо
гасит трату (pause). Тихий сбой = либо незаглушенный убыточный ад, либо двойной открут.

## Компоненты

### Точка входа: `apps/meta_api_worker/main.py`
- `main_loop` — поднимает `AuditedMetaApiClient`, redis, gather(task_loop, heartbeat_loop).
- `task_loop` — claim → `process_one_task`; на холостом ходу гоняет `maybe_refresh_account_tz` и
  `escalate_undelivered_autostop_pauses` (per-ad эскалация недоставленной паузы).
- `process_one_task` — полный жизненный цикл одной задачи: парс payload → асимметричный
  стоп-гейт (на паузе сканирования откладываем активирующие) → owner-scoping → execute →
  mark_succeeded/failed/requeue по классификации ошибки → FSM-sync → money-fail алерт.
- Классификация ошибок (`_PERMANENT_EXCEPTIONS` / `_TEMPORARY_EXCEPTIONS` / `_IRREVERSIBLE_KINDS`).

### Outbox-слой
- `core/tasks/queue.py` — каноническая unified-очередь для всех 5 task_type. `claim_next_task`
  (FOR UPDATE SKIP LOCKED), `mark_succeeded`/`mark_failed`/`requeue_for_retry` (bool-возврат +
  `WHERE status='running'` guard), `reconcile_stuck_running` (bump attempt_count),
  `fail_stuck_irreversible` (необратимые stuck-running → failed без retry).
- `core/meta_api/queue.py` — тонкая обёртка над выше для `task_type='meta_api_mutation'`:
  `create_mutation_task`/`create_draft_task`, `approve_draft_task` (owner ACL), `cancel_task`,
  `claim_pending_task`, прокси `mark_*`/`requeue_task`, `list_drafts`, `default_idempotency_key`.

### Mutation handlers: `core/meta_api/mutations/`
- `__init__.py` — `MUTATION_HANDLERS` (реестр по mutation_kind) + `dispatch_mutation`.
- `base.py` — Protocol `MutationHandler`, `require_numeric_id`, `success_result` (всегда
  `success=True`!), `require_status`.
- 10 handlers: `pause_ad`/`activate_ad`/`pause_campaign`/`activate_campaign`/`set_adset_budget`
  (hard cap $100k daily / $1M lifetime) / `duplicate_campaign` (Batch copy+rename) /
  `bulk_status_change` (Batch до 50) / `create_campaign` (Batch campaign+adset+creative+ad с
  JSONPath refs) / `custom_audience` / `set_ad_creative`.
- `_batch_helpers.py` — `encode_batch_body` + custom `_encode_value` (кодирует только
  `&+space%#\r\n` и не-ASCII, оставляет `{}:$.=` нетронутыми для JSONPath refs
  `{result=name:$.id}`), `make_batch_entry`, `build_batch_payload`, `parse_batch_response`,
  `jsonpath_ref`.

### Клиент: `core/meta_api/client.py` + `audit.py`
- `MetaApiClient` — gRPC к browser-agent (`ExecuteGraphCall`), circuit-breaker (3 фейла → OPEN
  60с), `check_health(full_probe)`. Маппинг gRPC-кодов и Graph-error в доменные исключения.
- `AuditedMetaApiClient(MetaApiClient)` — оборачивает `execute_graph_call`, пишет
  `meta_api_audit_log` (best-effort). Batch-ответ агрегирует http_status 200/207.

### Поддержка
- `schemas.py` — `MetaMutationPayload` (kind/target_id/params/ad_account_id), `MUTATION_KINDS`,
  `IRREVERSIBLE_MUTATION_KINDS`.
- `errors.py` — доменные исключения + `classify_graph_error` (subcode override → code lookup →
  дефолт; отрицательные коды browser-agent → Temporary).
- `fsm_sync.py` — `sync_fsm_after_mutation` приводит `ad_alert_state` к результату (pause→disabled,
  activate→normal); для bulk метит только `result['modified_ids']` (H2). `is_deactivating_bulk`.
- `ownership.py` — last-line-of-defense owner-scoping на исполнении (target → campaign_name из
  каталога, сверка owner_tag).
- `autostop_alert.py` — channel-down CRITICAL (N подряд сетевых фейлов pause_ad) + per-ad
  эскалация недоставленной паузы.
- `account_tz.py` — троттл-кэш TZ кабинетов.
- `reconciler.py` — НЕ запускается в проде (канонический reconciler покрывает meta-мутации).

## Последовательности вызовов

### Авто-стоп (главный money-путь)
```
observer pipeline (STOP-вердикт)
  └─ writers._create_pause_mutation(fb_ad_id, token)
       └─ create_mutation_task(kind=pause_ad, requested_by=bot_auto_stop,
                                idempotency_key=auto:pause_ad:{fb_ad_id}:{token},
                                max_attempts=15, status=pending)
            └─ INSERT task_queue ON CONFLICT (idempotency_key) DO NOTHING

meta_api_worker.task_loop
  └─ claim_pending_task  (UPDATE ... status=running, FOR UPDATE SKIP LOCKED)
  └─ process_one_task
       ├─ _is_activating_mutation? нет (pause) → не откладываем даже на паузе
       ├─ owner-scoping: check_mutation_ownership → _resolve_ad → campaign_matches_owner
       ├─ execute_mutation → dispatch_mutation → PauseAdHandler.execute
       │    └─ client.execute_graph_call(POST /{ad_id}?status=PAUSED, ad_account_id=...)
       │         └─ gRPC ExecuteGraphCall → browser-agent → page.evaluate(fetch graph)
       ├─ mark_task_succeeded (WHERE status=running)  → bool
       ├─ если applied: _publish_task_changed + sync_fsm_after_mutation (→ disabled)
       │                + record_autostop_success (re-arm)
       └─ при TemporaryError (Vision-канал мёртв, code=-2):
            requeue_task → backoff; если канал down → maybe_alert_autostop_channel_down
```

### Ручной / AI bulk / autostart
```
TG-callback / autostart cabinet_scheduler / AI-draft approve
  └─ create_mutation_task(kind=bulk_status_change|activate_ad, status=pending|draft)
  ... (draft → approve_draft_task с owner ACL → pending)
  └─ meta_api_worker → BulkStatusChangeHandler/ActivateAdHandler → Batch/single ExecuteGraphCall
  └─ sync_fsm_after_mutation: _sync_bulk метит только modified_ids
```

### Создание кампании (необратимый)
```
create_mutation_task(kind=create_campaign)
  └─ CreateCampaignHandler.execute
       ├─ build 4 batch entries (campaign/adset/creative/ad с jsonpath_ref)
       ├─ ExecuteGraphCall(POST / batch=...)
       ├─ parse_batch_response → extract_ids
       └─ если НЕ все success → raise CreateCampaignPartialError(created_ids, failed_steps)
  worker:
       ├─ CreateCampaignPartialError → mark_failed + лог осиротевших id
       └─ Temporary/ValueError/Exception → _fail_irreversible (НЕ requeue: риск дубля)
```

## Зависимости

**Подсистема зависит от:**
- `core/tasks/queue.py` (outbox-примитивы), `core/observer/queries.py`
  (`load_scanning_enabled`, `load_observer_config`, `campaign_matches_owner`),
  `core/observer/writers.py` (`reset_alert_state_after_*` для FSM-sync — обратная связь к observer).
- `clients/python_grpc/v1/meta_api_pb2*` (gRPC контракт с browser-agent).
- `core/telegram/worker_notify.py` (`notify_owners`/`notify_recipients` для money-fail/channel-down).
- `core/browser/circuit_breaker.py`.

**От подсистемы зависят:**
- observer (`writers._create_pause_mutation` создаёт задачу), cabinet_scheduler (autostart bulk
  activate), Telegram-хендлеры (`dis:`, draft confirm), AI-tools (`drafts/request_*`),
  reconciler_worker (`reconcile_stuck_running` + `fail_stuck_irreversible` для meta-мутаций),
  apps/api роутеры (disable/enable tasks читают через `channel.py`-предикаты).

**Общие структуры/контракты:** `MetaMutationPayload` (JSONB в `task_queue.payload`),
`Task` dataclass, `success_result`-shape (`{success, graph_response, modified_ids, ...}`),
`IRREVERSIBLE_MUTATION_KINDS` (единый для worker и reconciler).

## Потоки данных

- **task_queue** (Postgres) — единый outbox. Колонки: task_type, status, idempotency_key,
  payload(JSONB), attempt_count, max_attempts, requested_by, created_by_chat_id, next_retry_at,
  last_error, result(JSONB), completed_at. Статусы: draft→pending→running→succeeded/failed/retrying/cancelled.
- **MetaMutationPayload** — в payload: `{mutation_kind, target_id, params, ad_account_id}`.
  Сериализуется `to_dict`, читается `from_dict` в worker'е.
- **meta_api_audit_log** (Postgres, partitioned by month, retention 30д) — каждый Graph-вызов.
  Фильтрация чтения (`count_recent_calls`) по `created_at >= NOW() - interval` (партиционный ключ).
- **ad_alert_state** (Postgres FSM) — приводится FSM-sync'ом после mutation.
- **Redis:** `worker:heartbeat:meta_api` (TTL 60с), `fb_agent:task:changed` (pubsub),
  `autostop:net_fail_count`/`autostop:alerted` (channel-down дедуп),
  `autostop:undelivered:*` (per-ad эскалация дедуп/троттл), `meta_api:channel:health`
  (пишет health_watchdog, не worker).
- **gRPC ExecuteGraphCallRequest** — {session_id, method, endpoint, query_params, body_json,
  ad_account_id(без act_), timeout_ms} → ExecuteGraphCallResponse {status_code, response_json, error}.

## Внешние взаимодействия

- **browser-agent (gRPC :50051)** — единственный путь к Graph API (page.evaluate fetch внутри
  Vision). Circuit-breaker защищает от каскада.
- **Postgres** — outbox, audit, FSM, каталог (owner-scoping резолв).
- **Redis** — heartbeat, pubsub, дедуп/счётчики алертов.
- **Telegram** — money-fail/channel-down/undelivered DM owner'ам (best-effort).
- **Meta Graph API v22.0** — через browser-agent: single POST/GET и Batch (до 50 sub-requests).

## Инварианты и контракты (и где они хрупкие)

1. **Idempotency авто-стопа привязана к incident-токену** (`auto:pause_ad:{fb_ad_id}:{token}`).
   Re-stop после реактивации генерирует новый token → новая задача. Прочно.
2. **Необратимые kinds (create/duplicate) НИКОГДА не ретраятся** при transient/ValueError/unknown
   (риск дубля). Единый `IRREVERSIBLE_MUTATION_KINDS` для worker + reconciler (`fail_stuck_irreversible`).
   Прочно для `create_campaign`/`duplicate_campaign`. ХРУПКО: `bulk_status_change` НЕ в этом списке,
   хотя bulk activate тоже создаёт открут.
2bis. **`success_result` ВСЕГДА ставит `success=True`** — а worker НЕ инспектирует `result['success']`.
   Любой handler, вернувший «логический провал» без exception, помечается succeeded.
   ХРУПКО (см. findings F1/F2): bulk all-failed и duplicate rename-failed проходят как succeeded.
3. **Асимметричный стоп**: на паузе сканирования откладываются только активирующие мутации;
   выключающие (pause/bulk-pause) пропускаются. `is_deactivating_bulk` покрывает обе формы.
4. **Owner-scoping last-line-of-defense**: чужое → permanent fail; своё-не-в-каталоге →
   выключающее в requeue, включающее в fail; owner_tag пуст → ALLOW. Прочно.
5. **Race-guard outbox**: `mark_*` возвращают bool через `WHERE status='running'`; при False
   worker логирует и пропускает побочные эффекты (FSM-sync, alert). Прочно.
6. **FSM-sync best-effort** — не роняет succeeded-контракт; следующий observer-цикл всё равно увидит
   реальное состояние. Для bulk метит только `modified_ids`. Прочно.
7. **Audit best-effort** — не роняет основной запрос. Прочно.
8. **Batch JSONPath refs доходят до Meta нетронутыми** через `_encode_value`. Прочно (закрыт CRIT раунда 6).
