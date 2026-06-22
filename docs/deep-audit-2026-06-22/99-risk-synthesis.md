# FB Stop Bot — кросс-каттинг риск-синтез (deep audit 2026-06-22)

Сведение находок по 11 подсистемам **после адверсариальной верификации**. Severity здесь — финальная
(post-verdict): где верификатор написал `refuted` — находка выкинута, где `adjusted` — severity исправлена.
Дубли с предыдущим аудитом `docs/audit/AUDIT_2026-06-17.md` (snooze-suppress, partial-bulk FSM, FSM-залип
disabled, cold-tab token) не повторяются — здесь только НОВОЕ или углублённое.

---

## 1. Сводная таблица severity × подсистема (после верификации)

| Подсистема | CRIT | HIGH | MID | LOW |
|---|:--:|:--:|:--:|:--:|
| observer-core | — | 1 | 4 | 4 |
| meta-api | — | 1 | 4 | 4 |
| workers-money | — | 1 | 3 | 3 |
| workers-aux | — | 1 | 3 | 3 |
| data-layer | — | — | 4 | 4 |
| api-surface | 1 | 2 | 4 | 3 |
| telegram | — | — | 2 | 4 |
| browser-agent | — | 2 | 4 | 3 |
| frontend-web | — | 1 | 3 | 2 |
| frontend-mini | — | 1 | 2 | 5 |
| ancillary | — | — | 5 | 6 |
| **ИТОГО** | **1** | **11** | **38** | **41** |

Примечание: counts учитывают понижения верификаторов (например meta-api H2 bulk-activate → LOW;
enable_reco insert/send → LOW; ad_library full-scan ×3 HIGH→LOW; ads_admin gate HIGH→LOW; offers delete
CRIT→HIGH; dashboard_stats HIGH→MID; refresh-campaigns HIGH→refuted и исключён).

---

## 2. Топ-риски (money / security вперёд)

### CRIT

**R1. Orphan-задачи Meta-мутаций после bulk-delete объявлений** — `api-surface`
`apps/api/routers/v1/ads_admin.py:40`
`POST /dashboard/ads/bulk-delete` делает `DELETE FROM fb_ads` без проверки/отмены активных задач в
`task_queue`. Outbox не связан FK — CASCADE сносит `ad_alert_state`/`ad_metrics`, но orphan
`pause_ad`/`activate_ad` остаются. `PauseAdHandler.execute` бьёт по `payload.target_id` **без проверки
существования строки** → мутация применяется к живому объявлению в Meta без FSM-контекста.
**Money-грань: orphan `activate_ad` ре-включает открут на объявлении, которое оператор удалил из дашборда,
вслепую.** Эндпоинт реально вызывается фронтом (`useDeleteAds` в `routes/ads/index.tsx:215`).
Verdict: confirmed CRIT.
**Фикс:** в одной транзакции с DELETE — `UPDATE task_queue SET status='cancelled' WHERE payload->>'target_id'
= ANY(:ids) AND status IN ('draft','pending','running','retrying')`, либо 409 при наличии активных.

### HIGH

**R2. Naive SUM кумулятивных ad_metrics в enable-reco analyzer** — `workers-aux`
`core/enable_reco/analyzer.py:77-84,137`
`_aggregate_spend` суммирует кумулятивные снимки `ad_metrics` (spend плоский после паузы) → N снимков ×
S раздувают `total_spend`. Rule 1 (`total_spend <= cpa*0.5`) систематически false-negative — валидные
рекомендации включения подавляются. **Тот же класс, что CRIT-1 Round 10**, тест
(`test_enable_reco_analyzer.py:197`) вписывает баг (assert аддитивности на кумулятиве). Verdict: CRIT→HIGH
(advisory-путь с ручным подтверждением, Rule 1 — одно из 4 OR-условий, бюджет напрямую не тратится).
**Фикс:** `latest.spend` вместо суммы; Rule 1 = «текущий cabinet-day spend ≤ порог».

**R3. Bulk-стоп с полным отказом Meta фиксируется как succeeded без money-fail DM** — `meta-api`
`apps/meta_api_worker/main.py:419-447` + `mutations/bulk_status_change.py:132-143`
`process_one_task` после execute без exception безусловно зовёт `mark_task_succeeded` и НЕ читает
`result['success']`. Batch-конверт даёт HTTP 200, пер-саб ошибки живут в теле; при отклонении Meta ВСЕХ
sub-requests bulk-стоп метится succeeded, money-fail DM (только в except-ветках) не уходит — оператор видит
успех, ads тратят бюджет. Авто-стоп (single `pause_ad`) НЕ затронут (raise→except→DM), дыра в
ручном/AI bulk-pause. Verdict: confirmed HIGH.
**Фикс:** при `result.get('success') is False` → mark_failed + money-fail; либо bulk raise при `succeeded==0
and failed>0`.

**R4. NULL owner_tag в мульти-кабинете отключает owner-scoping → авто-стоп чужих ads** — `observer-core`
`core/observer/queries.py:319-321` + `apps/observer_worker/main.py:405`
При `len(accounts)>1` глобальный allowlist `campaign_ids` игнорируется (by design), скоупинг только через
owner_tag. При пустом owner_tag `campaign_matches_owner`→True для всех → чужой ад в shared-кабинете проходит
фильтр → FSM stop → `maybe_create_disable_task` создаёт `pending pause_ad` БЕЗ повторной проверки владельца и
БЕЗ draft → meta_api_worker паузит **чужую рекламу** (необратимо). Асимметрия: single-cabinet защищён
guard'ом пустого allowlist, multi-cabinet — нет. Verdict: confirmed HIGH.
**Фикс:** при `len(accounts)>1` и пустом owner_tag — отказ сканировать + CRITICAL/TG-ops, зеркаля
single-cabinet guard.

**R5. Self-heal браузер-сессии в мульти-кабинете лечит не ту вкладку / маскирует мёртвый кабинет** —
`browser-agent` (ДВА HIGH, одна корневая причина)
`session-manager.ts:476-482` + `session-health.ts:44-58`
(a) Level-0 heal делает `session.primaryPage.reload()`, но `ensureAdsManagerPage({actId})` осознанно НЕ
присваивает per-cabinet страницу в `primaryPage` → reload бьёт не по вкладке упавшего кабинета.
(b) `netFailureStreak`/`healLevel` живут на сессии (одной на профиль), не per-кабинет: успешный скан A
обнуляет streak даже если B накопил сбои → устойчиво мёртвый кабинет B никогда не лечится, канал
авто-стопа стоит; симметрично A-blip+B-blip = ложное лечение всей сессии. Порог «2 подряд» теряет смысл при
>1 кабинете. Verdict: оба confirmed HIGH.
**Фикс:** вести heal-state и таргет лечения per `(session, actId)` через Map; `recordFetchOutcome`/
`shouldHealNow`/`healSessionNetwork` принимают actId.

**R-money. Автостарт активирует мёртвые/старые объявления (is_active монотонно-TRUE)** — `workers-money`
`core/observer/writers.py` + `core/meta_api/bulk.py:99-122` + `cabinet_scheduler/main.py:144`
`fb_ads.is_active` ставится только в TRUE и НИГДЕ не сбрасывается. `resolve_owner_ad_ids_by_campaign_ids`
фильтрует по `is_active=TRUE` как «живые», без `last_seen_at`/даты. **Документированная защита по датам
`resolve_owner_ad_ids_by_dates` в коде отсутствует.** Резолвнутые id → `bulk_status_change activate` без
pre-flight → ранее снятые объявления реально активируются → нецелевой открут каждое утро. Радиус ограничен
owner-scoping+allowlist+gate; лексикографическое усечение `[:50]` сохраняет свежие. Verdict: confirmed HIGH.
**Фикс:** фильтр `last_seen_at >= cabinet_day_start` в резолве, либо сброс `is_active=FALSE` по stale last_seen.

**R-tma. Сломан «Сканировать сейчас» в TMA (404)** — `frontend-mini`
`frontend-mini/src/lib/api.ts:311`
`useTriggerScan` зовёт `/observer/scan-now` (не существует) вместо `/settings/observer/scan-now`. На двух
экранах (Dashboard+Settings) гарантированный 404. Verdict: confirmed HIGH (сломанная фича без обхода, но не
money/silent — observer-цикл не затронут). **Фикс:** заменить путь.

**R-offers. Удаление офферов из web-UI — no-op заглушка** — `frontend-web`
`frontend/src/routes/offers/index.tsx:267`
`deleteOfferFn` — пустое тело; рабочий `OfferDeleteManager`/`useDeleteOffer` рендерится только в тесте →
shape-not-semantics. Verdict: CRIT→HIGH (soft-delete, money-impact ноль, но фича сломана). **Фикс:** вызвать
`useDeleteOffer()` напрямую в onConfirm.

**R-ws. API-ключ в WS URL → утечка в логи** — `frontend-web`
`frontend/src/lib/websocket/useDashboardSocket.ts:107`
`?api_key=TOKEN` в WS URL оседает в nginx access_log/DevTools. Постоянный (не одноразовый) ключ.
Verdict: HIGH→MID (browser-limitation, тот же X-API-Key что у write-запросов, auth до accept()). **Фикс:**
short-lived ws-token (Redis SET NX TTL 60s) или убрать /ws из access_log.

---

## 3. Сквозные паттерны (повторяются в ≥2 подсистемах)

1. **Naive SUM кумулятивных ad_metrics.** Третий рецидив класса CRIT-1: enable_reco analyzer (R2,
   backend), Dashboard spend-chart (`frontend/src/routes/index.tsx:128`, `cumulativeSpendTotal` не
   используется). Правило держится комментариями/review, не типами/линтером — регресс не блокируется
   автоматически (data-layer LOW: перевести inline-DISTINCT-ON на хелпер + grep-guard в CI).

2. **Partition full-scan / промах pruning на горячем пути.** scan_runs `_finish_scan_run` WHERE id без
   started_at (workers-money MID); `/dashboard/stats` MAX(started_at) без границы (api MID, был HIGH —
   retention 30d ограничивает); enable_reco metrics без верхней границы (LOW); ad_library media/enricher/
   tier_ranker ×3 (LOW — индекс scan_id + 2-3 партиции при retention 14d). Закрытые верификатором как LOW
   всё равно сигналят: правило «фильтр по партиционному ключу» не enforced.

3. **Контракт writer↔reader держится дисциплиной, не типами.** observer:runtime (двойной), heartbeat-имена
   (5 копий heartbeat_loop), result['success'] (R3), Graph error code TS↔Python, ScannedAdRow ×3 места.
   Любой рассинхрон — тихий (история CRIT-2/Round 11).

4. **Сессионное/глобальное состояние там, где нужно per-cabinet/per-entity.** browser-agent heal-state
   (R5), `is_active` монотонный (R-money), `_graphContextCache`/`_tails`/`_client_cache` module-global без
   очистки (утечки памяти, MID/LOW).

5. **Идемпотентность/owner-проверка не на исполнении.** Orphan-задачи bulk-delete (R1), NULL owner_tag
   (R4), bulk idempotency_key не нормализует порядок list-id (meta-api LOW), default_idempotency_key
   sort_keys не сортирует списки.

6. **Документация расходится с кодом.** `resolve_owner_ad_ids_by_dates` документирована, отсутствует
   (R-money). topics.py/handlers/topics.py документированы в CLAUDE.md, отсутствуют (telegram — verdict
   LOW: намеренный переход на DM, вычистить доку). Docstring снуза «не отключает рекламу» не раскрывает
   подавление авто-стопа (observer MID). Стейл-комментарии ACL в alerts.py/creator.py (MID).

7. **Best-effort except глушит контрактные сбои.** FSM-sync reset-функции, `decrypt()` возвращает '' при
   InvalidToken без лога (ancillary MID — после ротации ключа без `rotate_encryption_key` все токены пусты,
   бот стартует и молча получает 401/403).

---

## 4. Рекомендованный порядок устранения

1. **R1 (CRIT)** — отмена orphan-задач при bulk-delete (необратимый ре-открут чужого/удалённого ад).
2. **R4 + R-money (HIGH money-необратимое)** — guard на NULL owner_tag в мульти-кабе; фильтр last_seen в
   автостарте. Оба про авто-действие над не теми деньгами.
3. **R3 (HIGH)** — worker читает result['success'] для bulk-стопа + money-fail DM.
4. **R5 (HIGH ×2)** — per-cabinet heal-state в browser-agent (мульти-кабинет — заявленный прод-режим).
5. **R2 (HIGH)** — latest вместо SUM в enable_reco; заодно Dashboard-chart (паттерн #1) + CI grep-guard.
6. **R-offers / R-tma (HIGH функциональные)** — починить сломанные UI-пути.
7. **R-ws / TMA-secret / decrypt-silent (MID security/hygiene)** — операционная гигиена ключей.
8. **Партиции next-month вне cleanup_worker (MID)** — ленивый CREATE или health-чек, чтобы простой воркера
   на стыке месяца не остановил весь money-поток записи.

Костяк (race-safe claim/mark, attempt_count канон, partition-pruning в 11/11 ad_metrics-сайтах, batch
JSONPath-encode, FSM-guards, ACL owner в TG/draft, money-граница latest-per-day в data-layer) подтверждён
качественным. Один CRIT и кластер HIGH сконцентрированы на стыках слоёв (outbox↔каталог, observer↔meta_api,
session↔cabinet) и на необратимых money-действиях без последней проверки.
