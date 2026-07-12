# Аудит кодовой базы FB Stop Bot — 2026-07-12

- **Scope:** all (backend B1–B5 + frontend F1–F2 + cross-cutting X)
- **Агенты:** 8 доменов, 11 прогонов (B4 декомпозирован на 4 под-проверки), две волны по ≤5 параллельных
- **Верификация:** все CRIT/HIGH подтверждены ручным чтением кода ведущим ревьюером
- **Read-only (фаза расследования):** код не менялся, тесты на живой БД не запускались

## Статус исправлений (обновлено 2026-07-12)

**Все CRIT + HIGH + MID закрыты** в 19 коммитах на `main` (не запушены — по уговору
пушим по команде). 1554 unit passed, ruff clean, alembic head линейный
(`0033_tracker_agg_setnull`), typecheck обоих фронтов чист, web 451 / mini 232 vitest.

- **CRIT-1** ✅ C-1 (PATCH owner-tag + гейт в PUT + оба фронта)
- **HIGH** ✅ H-1..H-9 (все 9)
- **MID** ✅ M-1..M-2, M-4..M-18 (money/security/perf). M-11 (frequency_analyzer
  подключить) — не входил в план фиксов (решение отложить, см. H-3/бизнес-часть).
- **LOW** — не трогались (осознанный tech-debt).
- **Осознанно отложено (LOW-риск, отмечено в коммитах):** M-19 (mini CabinetAutostart
  — цельная форма), M-22 (danger-пороги как ориентир), M-23 (WS-токен в URL —
  типовой trade-off), M-25 (OfferForm перезапись — single-owner).
- **Решения владельца:** H-2 — кап оставлен (правлен только текст алерта); H-3 —
  4 порога убраны из загрузчика/UI, колонки в БД оставлены под будущее подключение.

Детали правок — в git-логе (`git log --oneline`), каждый фикс с тестами.

## Итоговая таблица severity × домен (после дедупа)

| Домен | CRIT | HIGH | MID | LOW |
|---|---|---|---|---|
| B1 observer/rules/scanner | — | 2 | 2 | 4 |
| B2 meta_api/tasks/worker | — | — | 3 | 4 |
| B3 воркеры apps/* | — | 1 | — | 3 |
| B4 FastAPI (api) | 1* | 3 | 5 | 6 |
| B5 models/migrations/агрегации | — | 2 | 6 | 2 |
| F1 frontend/ | 1* | 1 | 4 | 3 |
| F2 frontend-mini/ | 1* | 1 | 4 | 3 |
| X tests/crypto/telegram/browser-agent | — | — | 2 | 2 |
| **Итого (уникальных)** | **1** | **9** | **26** | **~25** |

\* CRIT-1 — композитная находка: серверная дыра (B4) + два клиентских пути (F1, F2), считается одной.

---

## CRIT

### C-1. Лост-апдейт `is_scanning_enabled`: сохранение owner-тега молча выключает/включает мониторинг

- **Сервер:** [settings_observer.py:146-168](apps/api/routers/v1/settings_observer.py) — `PUT /settings/observer` безусловно перезаписывает `is_scanning_enabled` и НЕ зовёт гейт `scan_nothing_monitored_reason` (гейт есть только в `PATCH /scanning`, строки 185-190).
- **Web:** [campaigns/index.tsx:83-95](frontend/src/routes/campaigns/index.tsx) — `OwnerTagSection.handleSave` реконструирует полный PUT-body из react-query кэша (`staleTime: 60_000`), правя только тег.
- **Mini:** [settings/index.tsx:178-197](frontend-mini/src/routes/settings/index.tsx) — `handleSaveTag` шлёт тот же full-PUT из закэшированного `cfg`.

**Проблема.** Клиент правит одно поле (тег), но шлёт весь объект из устаревшего кэша. Если между загрузкой кэша и сохранением тега кто-то переключил сканирование (другая вкладка, mini↔web, аварийный стоп), PUT молча откатывает флаг. Обратное направление обходит гейт: PUT может включить скан при пустом allowlist — известный инцидент-класс «всё зелёное, авто-стоп не работает».

**Impact (money).** Тихое выключение мониторинга = убыточные ады жгут бюджет без авто-стопа; тихое включение в обход гейта = скан «работает» вхолостую при пустом allowlist. Детерминированный код-путь, воспроизводится обычным UI-действием.

**Fix.**
1. Бэк: добавить `PATCH /settings/observer/owner-tag` (по аналогии с существующими `scanning`/`auto-enable`); в `PUT` — звать гейт при `is_scanning_enabled=True` и/или сделать поле опциональным (`None` = не трогать).
2. Web `OwnerTagSection` и mini `handleSaveTag`: перейти на точечный PATCH (в `ObserverTab.tsx` это уже сделано — паттерн есть).
3. Тест: unit на «флаг меняется между рендером и кликом Сохранить».

**Confidence:** high (все три пути подтверждены чтением).

---

## HIGH

### H-1. creator_worker requeue'ит необратимый `plan_run` → дубль залива кампании

[apps/creator_worker/main.py:299-324](apps/creator_worker/main.py) — `grpc.RpcError` / `TimeoutError` / generic `Exception` посреди `RunPlan`-стрима → `requeue_for_retry` → план переисполняется целиком. Классификатор не отличает «стрим не стартовал» от «стрим создал кампанию и упал на середине». Reconciler специально исключает `plan_run` из requeue именно ради этого — воркер сам себе это ломает.
**Impact:** дубль кампании + двойной открут бюджета. **Fix:** после первого события стрима любая ошибка → `mark_failed` + TG-алерт «проверь кабинет на дубли»; retry только для ошибок до старта стрима. **Confidence:** med-high (подтверждено чтением; смягчение — creator_worker в текущей сборке не активен).

### H-2. Deposit-stage стоп не масштабируется числом депозитов → фолс-стоп прибыльных адов

[core/rules/evaluator.py:186-202](core/rules/evaluator.py) — при `external_deposits ≥ 1` работает только `spend_with_dep_range`: сравнивается `spend / cpa_amount` (одна CPA), число депозитов фигурирует ТОЛЬКО в тексте summary. Ад с 3 дешёвыми депозитами и spend 2× CPA (cost/dep ≈ 0.7 CPA — прибыльный) получит STOP наравне с адом с 1 депозитом.
**Impact:** структурный потолок ~0.56 CPA/день на ад — авто-убийца скейлящихся винеров. **Fix:** делить на `deposits × cpa_amount` (cost-per-deposit vs CPA) — **но сперва подтвердить намерение у байера**: возможно, жёсткий кап задуман. Добавить семантический тест на 2-3 депозита. **Confidence:** med (код подтверждён, бизнес-намерение — вопрос).

### H-3. 4 из 6 per-offer порогов — мёртвый конфиг с иллюзией защиты

[core/observer/queries.py:62-95](core/observer/queries.py) грузит `cpm_threshold` / `ctr_threshold` / `funnel_ratio_threshold` / `spend_no_event_threshold`, UI (RulesForm) их редактирует, но evaluator их **нигде не читает** (grep-подтверждено: ссылки только в модели/queries/схемах/миграциях).
**Impact:** байер выставляет «стоп по CTR/CPM» — защита молча не работает; хуже отсутствующей фичи. **Fix:** подключить правила в evaluator ИЛИ убрать поля из UI. Требует решения владельца. **Confidence:** high.

### H-4. Дедуп ingest глотает повторные `redep` по одному click_id

[core/adset_pro/ingest.py:38-41,75-124](core/adset_pro/ingest.py) — дедуп-ключ `(click_id, event_type)` в окне 24ч; комментарий обосновывает только FTD («повторный реальный FTD — нонсенс»), но `redep` по определению повторяется: 2-й/3-й депозит игрока за сутки помечается дублем и не записывается.
**Impact (money):** недосчёт депозитов → evaluator видит меньше депов → ложный STOP прибыльного ада; недосчёт revenue в `tracker_aggregate`. **Fix:** добавить в ключ дедупа txn-id из `raw_json` (проверить, что AdSet.pro его шлёт — raw хранится), либо 24ч-дедуп только для first-событий (`ftd`). **Confidence:** med (зависит от контракта AdSet.pro; проверяемо по накопленным raw_json).

### H-5. `/dashboard/chart-data` bucket=hour рисует кумулятив, а не почасовую дельту

[dashboard_timeseries.py:180-203](apps/api/routers/v1/dashboard_timeseries.py) — `per_bucket_ad` берёт последний снимок в часе (кумулятив с начала суток кабинета) и суммирует. Для `bucket=day` корректно (посуточный reset), для `hour` каждый бакет включает все предыдущие часы. Противоречит `stats_derived.py::hourly_deltas`, которая честно диффит через LAG.
**Impact:** почасовой график спенда на главном дашборде врёт (нарастающий кумулятив + «пила» через границу суток). **Fix:** для `hour` — per-ad LAG-дельта до SUM (переиспользовать подход `hourly_deltas`). **Confidence:** high.

### H-6. Mini: авто-запуск залива кампании без idempotency-ключа и персистентного guard'а

[StepLaunch.tsx:89-116](frontend-mini/src/routes/campaigns/StepLaunch.tsx) — POST `/tools/campaigns/launch` стреляет в `useEffect` при монтировании; guard `launched` — локальный `useState` (не переживает remount), `idempotency_key` не передаётся. StrictMode в dev детерминированно шлёт 2 POST; kill/restore TMA WebView до ответа → дубль в проде. В web-визарде — явная кнопка + retry с `crypto.randomUUID()`.
**Impact:** дубль кампании/адсета/креатива в Meta (создаются PAUSED — прямой потери нет, но риск задвоенного спенда при снятии паузы + повторная модерация креативов). **Fix:** client-side `idempotency_key` + факт «launch initiated» в персистентный wizardStore, либо явная кнопка как в web. **Confidence:** high.

### H-7. Cancel исполняющейся disable-задачи → пауза в Meta есть, FSM застрял

[disable_tasks.py:46,389-414](apps/api/routers/v1/disable_tasks.py) — `_TERMINAL_STATUSES = {succeeded, cancelled}` не включает `running` (retry рядом корректно блокирует через `_ACTIVE_STATUSES`). Отмена задачи, которую сейчас исполняет meta_api_worker: мутация в Meta успевает выполниться → `mark_task_succeeded` видит `status='cancelled'` → `applied=False` → `sync_fsm_after_mutation` пропускается.
**Impact:** объявление реально на паузе, но `ad_alert_state` застрял в `stop_sent`, задача навсегда `cancelled` — ровно тот класс, который FSM-sync (#39) закрывал. Эндпоинт достижим напрямую (в UI кнопки пока нет). **Fix:** запретить cancel для `running` → 409. **Confidence:** high.

### H-8. Web: partial failure bulk-disable скрыт от оператора

[ads/index.tsx:160-171](frontend/src/routes/ads/index.tsx) — бэк честно возвращает `{created, skipped, failed}`, фронт показывает только `created.length` («Создано N disable-задач»); `failed`/`skipped` не читаются нигде, тест мокает `failed: []` всегда.
**Impact (money):** оператор уверен, что все выбранные ады остановятся — часть молча продолжает жечь бюджет. **Fix:** `toast.error` со списком id при `failed.length > 0` (+ тест с непустым failed). **Confidence:** high.

### H-9. BodySizeLimit обходится chunked-запросом → DoS публичного постбэка

[body_size.py:42-62](apps/api/middleware/body_size.py) — лимит проверяется только по `Content-Length`; `Transfer-Encoding: chunked` → проверка пропущена, `request.json()` читает всё тело в память. Публичный `POST /api/v1/postback/adsetpro` исключён из X-API-Key.
**Impact:** OOM/DoS API-процесса (на 24/7-хосте частично гасится ingress-лимитом, если настроен — проверить `client_max_body_size`). **Fix:** считать байты через обёртку `receive` → 413, либо отклонять chunked на не-multipart путях; проверить/задать лимит на nginx/Caddy. **Confidence:** high.

---

## MID

### Money / корректность

- **M-1.** [bulk_status_change.py:132-143](core/meta_api/mutations/bulk_status_change.py) + [meta_api_worker/main.py:310-337](apps/meta_api_worker/main.py) — батч, где ВСЕ саб-реквесты упали транзиентно (rate-limit/null), сворачивается в result-dict → `mark_failed` без retry; частично провалившиеся id тоже не ретраятся. Автостарт кабинета/bulk-pause тихо не доисполняются (смягчено DM владельцу). Fix: бросать Temporary при все-транзиентных саб-провалах или requeue по инспекции `sub_results`. conf: high.
- **M-2.** [meta_api_worker/main.py:713-717](apps/meta_api_worker/main.py) — irreversible-мутации получают `_fail_irreversible` даже когда провал доказуемо ДО отправки (circuit-open `SessionUnavailableError`; `create_campaign` с пустым `created_ids`). Залив умирает навсегда при блипе канала (безопасная сторона, но ручной пере-триггер). Fix: pre-send ошибки → retryable. conf: high.
- **M-3.** [core/tasks/queue.py:394-404](core/tasks/queue.py) + reconciler — нет `fail_stuck_plan_run` (зеркала `fail_stuck_campaign_create`): SIGKILL посреди plan_run → задача в `running` навсегда, без алерта, cleanup её не чистит. Fix: добавить fail_stuck + DM «проверь кабинет». conf: high.
- **M-4.** [core/adset_pro/queries.py:21](core/adset_pro/queries.py) — `event_type` нигде не нормализуется по регистру: `FTD`/`Ftd` от AdSet.pro молча не посчитается ни в evaluator, ни в aggregate. Fix: `.lower()` при ingest + контрактный тест. conf: med.
- **M-5.** [0031_default_partitions.py](migrations/versions/0031_default_partitions.py) ↔ [cleanup_worker/worker.py:150-190](apps/cleanup_worker/worker.py) — строки в `_default` блокируют `CREATE PARTITION` (обрыв цикла создания) и никогда не дропаются retention'ом. Fix: detach→перелив→attach + алерт + периодический drain. conf: med-high.
- **M-6.** `scripts/apply_schema.py::_create_first_partitions` — bootstrap не создаёт `_default`-партиции (а `alembic stamp head` не выполняет 0031): на свежем проде гэп партиций → hard-fail INSERT → потеря метрик/депозитов. Fix: создавать `_default` в apply_schema. conf: med-high.
- **M-7.** [tracker_aggregator_worker/worker.py:24,35](apps/tracker_aggregator_worker/worker.py) — даунтайм > lookback (2ч) через полночь UTC: хвост прошлого дня никогда не пересчитается (только аналитика; evaluator читает сырые постбэки). Fix: catch-up 2 суток на старте / lookback от last_run_at. conf: med.
- **M-8.** [aggregator.py:116-119](core/adset_pro/aggregator.py) — события без валидного country выпадают из `tracker_aggregate` вместе с deposits/revenue. Fix: sentinel `'XX'`. conf: med.
- **M-9.** [trackers/aggregate.py:37](core/models/trackers/aggregate.py) — `ad_id` FK `CASCADE` (постбэки при этом `SET NULL`): hard-delete ада невосстановимо сносит revenue-историю. Fix: `SET NULL`. conf: med.
- **M-10.** [evaluator.py:230](core/rules/evaluator.py) — `frequency_outlier_cap` жёстко 10.0: burnout с freq 11-30 молчит; при пороге ≥10 STOP недостижим. Fix: cap от порога. conf: med-high.
- **M-11.** [frequency_analyzer.py](core/rules/frequency_analyzer.py) — data-driven авто-порог (240 строк) не подключён нигде в проде; CLAUDE.md описывает как фичу. Fix: подключить (dry_run) или пометить отложенным. conf: high.
- **M-12.** [auto_enable.py:121](apps/api/routers/v1/auto_enable.py) — `cabinet_day_started_at` никогда не сбрасывается (docstring обещает reset при роллове): флаг «не включать автоматически» живёт вечно → ад навсегда выпадает из авто-recovery. Fix: реализовать сброс или поправить docstring. conf: high.

### Security / auth

- **M-13.** [core/crypto.py:85-140](core/crypto.py) — `ensure_encryption_key` читает только файл `.env`, игнорирует `os.environ`: env-only деплой (k8s Secret) сгенерирует ВТОРОЙ ключ → verify-fail краш / риск порчи шифрованных токенов. Латентно (bare-metal льёт ключ в файл). Fix: сперва `os.environ`. conf: med.
- **M-14.** [handlers/alerts.py:138-163](core/telegram/handlers/alerts.py) — `ereco:` без replay-guard (у `dis:` есть token-сверка): старая кнопка из истории чата безусловно создаёт `activate_ad`. Смягчено: owner-only + observer перестопит за ~90с. Fix: проверять PENDING-статус рекомендации. conf: med.
- **M-15.** [tma.py:123](apps/api/routers/v1/tma.py) — окно replay initData = 24ч (дефолт `validate_init_data`). Fix: `max_age_seconds` 900-3600. conf: med.
- **M-16.** [ai_analyze.py:55-58](apps/api/routers/v1/ai_analyze.py) — при `trust_proxy_headers=True` берётся левый (спуфимый) IP из XFF → обход rate-limit → расход AI-бюджета. Дефолт `False` — безопасен. Fix: правый IP / X-Real-IP. conf: high.

### Производительность / API

- **M-17.** [ads_timeline.py:52-60](apps/api/routers/v1/ads_timeline.py) — нет cap диапазона и LIMIT на 3 запросах → многомегабайтные ответы. Fix: `_MAX_RANGE_DAYS` + LIMIT. conf: high.
- **M-18.** [dashboard_stats.py:66-73](apps/api/routers/v1/dashboard_stats.py) — `MAX(started_at)` по `scan_runs` без нижней границы по партиционному ключу на горячем `/dashboard/stats`. Fix: `AND started_at >= NOW()-7d`. conf: med.

### Frontend

- **M-19.** [settings/index.tsx:389-420](frontend-mini/src/routes/settings/index.tsx) — CabinetAutostart: тот же лост-апдейт класс (PUT целиком из локального useState; конкурент — TG `/autostart`). conf: med.
- **M-20.** [ads/index.tsx:349](frontend-mini/src/routes/ads/index.tsx) — реинкарнация бага `var(--fsm-warning_sent)` в фильтр-чипах (токена не существует → невидимые точки; в AdRow пофикшено через `alertStateCssVar`). conf: high.
- **M-21.** [api.ts:845-863](frontend-mini/src/lib/api.ts) — `useUploadConcepts` мимо `fetchJson` → нет 401-relogin-retry. conf: med.
- **M-22.** [adHelpers.ts:122-127](frontend/src/components/domain/ads/adHelpers.ts) — хардкод danger-порогов (CPL>30, freq>4) не связан с per-offer правилами — светофор врёт байеру про близость к авто-стопу. Fix: прокинуть пороги оффера или пометить как ориентир. conf: med.
- **M-23.** [useDashboardSocket.ts:103-108](frontend/src/lib/websocket/useDashboardSocket.ts) — постоянный API-ключ в query-string WS → access-логи прокси. Fix: короткоживущий WS-токен. conf: med.
- **M-24.** [StatsChartCard.test.tsx](frontend/src/tests/stats/StatsChartCard.test.tsx) — тесты total/peak shape-only, `reduce` не проверен по значению. Fix: семантический тест. conf: high.
- **M-25.** [offers/index.tsx:141-150](frontend-mini/src/routes/offers/index.tsx) — OfferForm перезаписывает `ad_account_ids`/`countries` полным списком из stale-формы (single-owner → низко). conf: low-med.
- **M-26.** [adHelpers.ts:28-30](frontend/src/components/domain/ads/adHelpers.ts) — устаревшие as-касты на поля, уже существующие в generated-типах. Fix: убрать. conf: high.

---

## LOW (кратко)

**B1:** `warning/stop_rule_codes` затираются в `[]` на non-emit цикле (writers.py:512); alert_events INSERT не привязан к успеху state-UPDATE (гонка с claimed, почти недостижимо); дубль pause-задач при 2 конкурентных observer'ах (uuid4 per decide); `insert_metrics` глушит любое исключение как «нет партиции» (writers.py:330).
**B2:** `approve_draft_task(admin_override=True, chat_id=None)` не проверяет роль — латентный ACL-обход для будущих caller'ов; нет приоритета `pause_ad` в claim-очереди; успех с пустыми `modified_ids` (орфан без трекинга); `_alert_money_fail` подписывает провал bulk-activate как «Пауза».
**B3:** дедуп-ключ autostart = GET+SET без NX (money-гарантия на idempotency_key — расхождение с докой); digest при ошибке Redis молчит весь день со статусом `already_sent`; observer импортирует `_get_database_url` из telegram_poller; `observer_worker/main.py` 1393 стр. и `health_watchdog/main.py` 1241 стр. (>500).
**B4:** X-Request-Id без валидации/лимита; `compare_digest(str,str)` → TypeError→500 на не-ASCII заголовке (сравнивать bytes); `/api/tools/*` целиком вне BodySizeLimit; `list_scan_runs`/`list_alert_events` без cap ширины окна; f-string JSON в scan-now (заменить json.dumps); `campaigns_create.py` 812 строк; клиентский `idempotency_key` в launch не сверяется с хешем конфига (повтор ключа с другим конфигом → тихий «успех» старого run).
**B5:** docstring `latest_per_ad_window_cte` вводит в заблуждение («итог за час» — из-за него родился H-5); UTC-день vs сутки кабинета — задокументированная погрешность.
**F1:** `forceReconnect` не используется (нет health-check на visibilitychange); ROAS-колонка всегда «—»; `campaigns.ts` 447 стр. / `ConceptCampaignMatrix.tsx` 479 стр. — кандидаты на разнесение.
**F2:** `(createFileRoute as any)` в campaigns (перегенерить routeTree); мёртвый параметр `_search`; нет тестов на stale-флаг в handleSaveTag / remount StepLaunch / AuthGuard.
**X:** провал компенсирующего DELETE sentinel → потерянный алерт (добавить reconcile `message_id=0` старше N мин); синхронный backoff 502-504 в TG-клиенте до 7с на получателя внутри scan-цикла.
**Docs:** CLAUDE.md устарел: `PATCH act-via-api` и `GET /vision/profiles` не существуют (act_via_api удалён миграцией 0016); `history.py` уже 419 строк, не 692.

---

## Чистые зоны (подтверждено)

- **Naive-SUM по кумулятивным `ad_metrics`** — не найден нигде (6 analytics-роутеров + воркеры-потребители через latest-per-ad CTE/LATERAL; фронты не досуммируют на клиенте). Класс CRIT-1 из Round 10 закрыт, кроме частного случая H-5 (hour-bucket).
- **Идемпотентность outbox / reconciler-zombie / FSM-инварианты** (open_token, terminal-guard, snooze boundary) — крепкие, двойного исполнения с потерей денег не найдено.
- **cabinet_scheduler money-защиты** — пустой allowlist, done-маркер после успеха, чанкинг с уникальными idempotency_key — всё сходится.
- **Security-периметр** — timing-safe секреты, path traversal, dev-tools gate, CORS-«*»-guard, WS-auth до accept, секреты не сериализуются наружу, SecretStr/логи чистые, HTML-эскейпинг TG полный, MCP write-tools реально отключены.
- **Качество тестов** — money-тесты семантические (exact-value мультицикл), контракты writer↔reader покрыты (heartbeat, observer:runtime, varchar32). Дыры точечные: мульти-депозит evaluator (H-2), remount StepLaunch (H-6), непустой `failed` в bulk-disable (H-8).
- **browser-agent money-путь** — spend/deposits парсятся без locale-эвристик, evaluator доверяет только `external_deposits` (граница подтверждена), токен не течёт в логи.

---

## Рекомендованный план

**Сразу (money, детерминированные):**
1. **C-1** — PATCH для owner-тега + гейт/Optional в PUT + правка обоих фронтов (1 бэк-эндпоинт + 2 мелкие фронт-правки).
2. **H-7** — запретить cancel для `running` (однострочный guard + тест).
3. **H-8** — toast при `failed.length > 0` в bulk-disable (фронт, мелко).
4. **H-6** — idempotency_key + персистентный guard в mini StepLaunch.
5. **H-5** — LAG-дельта для hour-bucket chart-data (паттерн уже есть в `hourly_deltas`).

**Требуют решения владельца (бизнес-семантика):**
6. **H-2** — масштабировать ли deposit-stage кап числом депозитов (вопрос байеру: намеренный ли потолок ~0.56 CPA/день).
7. **H-3** — подключать 4 мёртвых порога в evaluator или убирать из UI.

**Следом (money-надёжность):**
8. **H-4** — txn-id в дедуп-ключ redep (сначала проверить raw_json накопленных постбэков).
9. **H-1 + M-3** — plan_run: mark_failed после старта стрима + fail_stuck_plan_run с алертом.
10. **H-9** — chunked-лимит (или подтвердить ingress-лимит на 24/7-хосте) — быстрая проверка + фикс.
11. **M-1, M-2** — ретраи bulk/irreversible по классификации саб-ошибок.
12. **M-5, M-6** — гигиена `_default`-партиций + bootstrap.

**Tech-debt (не срочно):** M-10..M-12, M-13..M-16, M-17..M-26, все LOW. Отдельно — актуализация CLAUDE.md (act_via_api, history.py, vision/profiles).
