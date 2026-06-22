# Аудит: Meta API + мутации + outbox-исполнитель (2026-06-22)

Read-only аудит подсистемы `core/meta_api/`, `core/tasks/`, `apps/meta_api_worker/`.
Известные долги из `docs/audit/AUDIT_2026-06-17.md` (H1-H5, M1-M3, L1-L15) НЕ повторяются —
они закрыты. Ниже — НОВЫЕ находки, в первую очередь на семантической границе
«handler вернул логический провал, но без exception».

Сводка: CRIT 0 · HIGH 2 · MID 4 · LOW 3.

---

## HIGH

### H1 — Логический провал mutation (`success=False`) маскируется под `succeeded`: задача закрывается, провал не виден, money-fail алерт не шлётся
- **location:** `apps/meta_api_worker/main.py:418-447` (worker не читает `result["success"]`);
  `core/meta_api/mutations/base.py:79-96` (`success_result` всегда `success=True`);
  `core/meta_api/mutations/bulk_status_change.py:132-143`;
  `core/meta_api/mutations/duplicate_campaign.py:164-211`.
- **problem:** `process_one_task` после `execute_mutation` без исключения БЕЗУСЛОВНО зовёт
  `mark_task_succeeded`. Он никогда не инспектирует `result["success"]`. Два handler'а возвращают
  «логический провал» без raise: (а) `bulk_status_change`, когда ВСЕ sub-requests упали, всё равно
  возвращает `success_result(...)` (а тот хардкодит `success=True`) с `modified_ids=[]`, `failed=N`;
  (б) `duplicate_campaign._execute_with_batch` явно возвращает `{"success": False, ...}` при провале
  copy или rename. В обоих случаях worker метит задачу `succeeded`.
- **impact:** Bulk-пауза/стоп, где Meta отклонила все объекты (например, истёкший токен на части,
  permission, неверный id), записывается как успешная — оператор/дашборд видит «успех», объявления
  продолжают тратить бюджет, money-fail DM НЕ уходит (`_alert_money_fail` вызывается только в except-
  ветках). Для `duplicate_campaign` `success=False` (copy создан, rename упал) тоже становится
  `succeeded` — рассинхрон с реальностью и потеря сигнала о ручной правке.
- **fix:** В `process_one_task` после `execute_mutation` проверять `result.get("success") is False`
  → трактовать как провал: `mark_failed` (для duplicate — без retry, kind необратимый) или, для bulk
  с partial-fail, поднять долю провала в last_error + money-fail алерт при `_PAUSE_KINDS`. Альтернатива:
  `bulk_status_change` при `failed>0 and succeeded==0` должен `raise`, а не возвращать success_result.
- **confidence:** high

### H2 — `bulk_status_change` отсутствует в `IRREVERSIBLE_MUTATION_KINDS`, но bulk-activate тратит бюджет; transient-ошибка после возможного коммита Meta → повторное включение
- **location:** `core/meta_api/schemas.py:40-45` (список); `apps/meta_api_worker/main.py:513-549`
  (transient → requeue для не-irreversible); `core/meta_api/mutations/bulk_status_change.py`.
- **problem:** Bulk Batch API НЕ атомарен и НЕ идемпотентен по строке (idempotency_key защищает
  только enqueue). Если bulk activate частично применился в Meta, но gRPC-ответ потерялся
  (DEADLINE/UNAVAILABLE после коммита) → `TemporaryError` → `requeue_task`. На повторе те же id
  снова шлются в Batch. Для activate это повторное включение уже включённых ads (для pause —
  безвредно, идемпотентно). `bulk_status_change` НЕ входит в `_IRREVERSIBLE_KINDS`, поэтому
  money-safe «не ретраить после возможного коммита» к нему не применяется.
- **impact:** Re-включение ранее включённых объявлений после потерянного ответа — лишний открут на
  объявлениях, которые оператор мог уже выключить между попытками. Меньше create-дубля, но money-риск
  реален на autostart bulk-activate (money-критичный автомат cabinet_scheduler).
- **fix:** Для activate-формы bulk применять ту же логику, что и для необратимых: при transient после
  первой попытки не слепо requeue, а сверять фактический статус (pre-flight GET) либо метить failed.
  Минимально — документировать и не ретраить bulk-activate после DEADLINE (только bulk-pause безопасен
  для retry, т.к. идемпотентен).
- **confidence:** med

---

## MID

### M1 — Owner-scoping открывает по 1-6 отдельных connection на каждую задачу (N коротких транзакций на горячем пути)
- **location:** `core/meta_api/ownership.py:70-180` (каждый `_resolve_*` / `_resolve_*_batch`
  открывает свой `engine.connect()`); вызывается в `process_one_task:374-375` на КАЖДУЮ задачу.
- **problem:** На каждую mutation: `load_owner_tag` (→ `load_observer_config`, отдельный connect)
  + `check_mutation_ownership` (ещё один connect). Для bulk — один batch-connect, ок. Но
  `load_owner_tag` читается «per-task без кэша» осознанно (money-настройка), удваивая round-trips.
- **impact:** При высоком темпе авто-стопа/bulk — лишняя нагрузка на пул соединений; не критично
  при текущих объёмах, но на пути изменений. Не money-баг.
- **fix:** Объединить `load_owner_tag` + ownership-резолв в одну транзакцию/connection; либо
  micro-кэш owner_tag в Redis с TTL 5-10с (money-настройка применится почти мгновенно).
- **confidence:** med

### M2 — FSM-sync для bulk: N последовательных транзакций (по одной на каждый ad)
- **location:** `core/meta_api/fsm_sync.py:104-110` (`for fb_ad_id in ad_ids: await reset(engine, ...)`).
- **problem:** `_sync_bulk` вызывает `reset_alert_state_after_*` в цикле, каждый вызов открывает
  отдельный `engine.begin()`. До 50 транзакций подряд после одного bulk.
- **impact:** Лишние round-trips после успешного bulk (≤50, ограничено MAX_BATCH_ENTRIES). Не money-
  баг, но заметный долг при росте bulk-объёмов; задерживает закрытие задачи.
- **fix:** Один UPDATE с `WHERE ad_id IN (SELECT id FROM fb_ads WHERE fb_ad_id = ANY(:ids))
  AND alert_state IN (...)` вместо цикла.
- **confidence:** high

### M3 — `set_adset_budget` / create_campaign cap зашит в код, но прямой MCP/HTTP-вызов с большим бюджетом проходит owner-scoping и cap, лимит не настраивается per-offer
- **location:** `core/meta_api/mutations/set_adset_budget.py:38-39,93-107`;
  `core/meta_api/mutations/create_campaign.py:578-592`.
- **problem:** Hard cap $100k daily / $1M lifetime — единственный money-guard на величину бюджета.
  Это защита от hallucinated-значений, но НЕ от легитимно-большого ошибочного значения в пределах
  cap (например, $99 999/день вместо $99). Нет per-offer/per-account потолка и нет draft-обязательности
  для budget-мутаций (в отличие от pause, budget — не выключающее действие).
- **impact:** Ошибочный бюджет в пределах cap откручивается без подтверждения. Money-риск средний
  (cap ограничивает катастрофу, но не порядок ошибки).
- **fix:** Для `set_adset_budget`/create с бюджетом — требовать DRAFT-подтверждение (как pause),
  либо ввести per-account мягкий порог из конфига с алертом при превышении среднего.
- **confidence:** med

### M4 — `parse_batch_response` для `null`-элемента (timeout/skipped sub-request) ставит `code=0` → `success=False`, но в create_campaign это попадает в `failed_steps` без различения «не выполнен» и «упал»
- **location:** `core/meta_api/mutations/_batch_helpers.py:194-206` (`null` → `missing_sub_result`,
  code=0); `create_campaign.py:241-266`.
- **problem:** Meta при overload возвращает `null` для части batch entries (sub-request не исполнен).
  Парсер метит его `success=False, error=missing_sub_result`. В `create_campaign` это даёт
  `CreateCampaignPartialError` даже если объект НЕ был создан (просто не выполнен). Логика «осиротевшие
  объекты» корректна (created_ids фильтрует None), но `failed_steps` смешивает «упал» и «не выполнен» —
  оператор может зря искать осиротевший объект, которого нет.
- **impact:** Ложная тревога о ручной чистке Meta; сам по себе не money-loss, но усложняет
  recovery необратимой операции. Низко-средне.
- **fix:** Различать code=0/null («не выполнен, объекта нет») от code>=400 («упал, мог создаться») в
  failed_steps; в логе явно помечать «возможен коммит» только для второго.
- **confidence:** med

---

## LOW

### L1 — Дублирование reconcile-логики: `core/meta_api/reconciler.py` живёт, но «в проде не запускается»; `reconcile_stuck_meta_running` НЕ бампает attempt_count
- **location:** `core/meta_api/reconciler.py:27-55`.
- **problem:** Мёртвый-по-назначению модуль (комментарий явно говорит «не запускается»), но публичный
  и импортируемый. `reconcile_stuck_meta_running` без bump attempt_count — если кто-то его вызовет,
  необратимая mutation может ретраиться сверх лимита (тот самый money-баг, ради которого его убрали).
- **impact:** Copy-paste/misuse-trap. Прямого вреда нет, пока не вызывается.
- **fix:** Удалить модуль или оставить только `cancel_stale_meta_drafts`; `reconcile_stuck_meta_running`
  пометить deprecated/убрать.
- **confidence:** high

### L2 — `bulk_status_change` docstring/код предупреждают, что object_type↔object_ids не верифицируется, но handler шлёт `POST /{id}?status=` без pre-flight — caller-доверие как единственная защита
- **location:** `core/meta_api/mutations/bulk_status_change.py:39-53,105-118`.
- **problem:** Если caller передал `object_type='campaign'` со списком ad-id (или наоборот), Meta
  применит status к НЕ тем сущностям — «последствия необратимы» (по собственному комментарию).
  owner-scoping (`_check_bulk`) резолвит id по object_type из локального каталога, что частично ловит
  несоответствие (id не найдётся как campaign), но не гарантирует.
- **impact:** Потенциально необратимое действие на чужой/не-той сущности при баге caller'а.
  Реальные callers (autostart, AI request_bulk_pause) формируют список из БД, риск низкий.
- **fix:** Опциональный pre-flight `GET /{id}?fields=id` на тип, либо валидация что object_type
  совпадает с тем, как id резолвится в owner-scoping (переиспользовать тот резолв как pre-flight).
- **confidence:** low

### L3 — `default_idempotency_key` сериализует `params` через `json.dumps(sort_keys=True)`, но вложенные dict в params с не-сортируемыми/нестабильными порядками ключей дадут разные хеши для логически одинаковых payload
- **location:** `core/meta_api/queue.py:41-67`.
- **problem:** Для не-auto путей (ручной/AI) idempotency_key зависит от точного JSON params. Два
  логически идентичных bulk с разным порядком id в списке (`[a,b]` vs `[b,a]`) дадут разные ключи →
  не сдедуплицируются. sort_keys сортирует ключи dict, но НЕ элементы list.
- **impact:** Дубль-задача при повторном ручном/AI-вызове с переставленным списком. Bulk-pause
  идемпотентен в Meta (безвреден), но bulk-activate/budget — нет. Низкий (callers обычно стабильны).
- **fix:** Для bulk нормализовать (сортировать) списки id перед хешированием, либо документировать,
  что caller отвечает за стабильный порядок.
- **confidence:** low

---

## Зона без находок (проверено, чисто)

- **Idempotency авто-стопа** (`auto:pause_ad:{fb_ad_id}:{token}`) — корректна, re-stop → новый token.
- **`_encode_value` JSONPath refs** — корректно сохраняет `{}:$.=` (закрыт CRIT раунда 6).
- **Race-guard `WHERE status='running'`** в mark_* — корректен, FSM-sync/alert пропускаются при race-loss.
- **`fail_stuck_irreversible` + `reconcile_stuck_running(exclude_kinds)`** — двойная защита от
  ретрая необратимых, bump attempt_count только в каноническом reconciler.
- **partition-pruning audit** (`count_recent_calls`) — фильтр по `created_at` (партиционный ключ), ОК.
- **approve_draft_task ACL** — admin_override верифицирует `is_admin_recipient`, chat_id-match путь
  owner-гейтится на периметре (закрыт CRIT раунда 6/9).
- **Асимметричный стоп-гейт** — `is_deactivating_bulk` покрывает обе формы bulk, money-safe.
