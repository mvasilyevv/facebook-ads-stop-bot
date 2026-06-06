# Аудит кодовой базы FB Stop Bot — 2026-06-06

**Scope:** вся кодовая база (команда `/audit`, боевой полный прогон).
**Метод:** 7 субагентов (read-only) по 8 доменам + ручная верификация всех ключевых CRIT/HIGH по исходникам. Изменений в код не вносилось. `ruff check core/ apps/ clients/ tests/` — чисто (13 ошибок только в throwaway `scripts/recon_*.py`, вне домена).

Домены:
- **B1** `core/observer` + `core/rules` + `core/scanner` (детект, FSM, стоп-правила) — opus
- **B2** `core/meta_api` + `core/tasks` + `apps/meta_api_worker` (мутации, outbox) — opus
- **B3** `apps/*_worker` (воркеры/шедулеры) + `core/worker_lock`/`scheduler`/`control` — opus
- **B4** `apps/api` (FastAPI routers v1) — sonnet
- **B5** `core/models` + `migrations` + `core/dashboard` + `core/adset_pro` — opus
- **F1** `frontend/` (новый TS strict) — sonnet
- **F2** `frontend-mini/` + `frontend-legacy/` — sonnet
- **X** `tests/` + cross-cutting (`crypto`/`config`/`ai_assistant`/`telegram`/browser-agent TS) — opus

## Сводная таблица severity × домен (до дедупа)

| Severity | B1 | B2 | B3 | B4 | B5 | F1 | F2 | X | **Σ** |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 🔴 CRIT | 1 | 1 | 0 | 1 | 0 | 2 | 1 | 0 | **6** |
| 🟠 HIGH | — | 3 | 2 | 3 | 2 | 4 | 3 | 2 | **19** |
| 🟡 MID | 3 | 3 | 2 | 3 | — | 5 | 3 | 4 | **23** |
| ⚪ LOW | 2 | 2 | 5 | 6 | 3 | 4 | 2 | 3 | **27** |

> ~6 находок дублируются между доменами (disable/enable task_type-рассинхрон засекли B4+B5+F2; два reconciler'а — B2+B3; fan-out spend — B4+B5). Ниже сгруппированы по темам, дубли слиты.

---

## ⭐ ГРУППА 0 — Регресс от свежего DOM-removal мерджа (приоритет)

> **Главное открытие.** Только что влитый стек (удаление DOM-toggle, эндпоинтов `act_via_api` и `disable-worker/restart`, воркеров disable/enable) оставил хвосты: **фронт и API-чтение не обновили под удаление**. Money-путь (детект→стоп→`pause_ad`) работает, но наблюдаемость отключений и часть UI сломаны. Это не слив денег, но прямой регресс нашей работы — закрыть первым.

### G0-1 [CRIT] Ручные disable-задачи невидимы и неуправляемы в UI
- **where:** `apps/api/routers/v1/disable_tasks.py:74` (GET), retry/cancel (`:~157,231,318`); `enable_tasks.py:60` (GET)
- **problem:** GET/retry/cancel фильтруют `task_type='disable'`, но POST уже создаёт `meta_api_mutation`+`pause_ad` (проверено построчно). Созданная задача не появляется в списке, retry/cancel по id → 404. enable — read-only, тот же эффект (пустой список).
- **impact:** 💸 наблюдаемость: оператор не видит, что бот ставит/провалил паузу → может думать, что ад не отключается, и сливать дальше.
- **fix:** фильтр → `task_type='meta_api_mutation' AND payload->>'mutation_kind'='pause_ad'` (и `activate_ad` для enable). 1 место чтения + retry/cancel.
- **confidence:** high ✓verified

### G0-2 [HIGH] Дашборд/History counters отключений покажут ~0
- **where:** `apps/api/routers/v1/dashboard_stats.py:136-162,223-248`; `core/dashboard/history_queries.py:103-118,124-176,432-441` (8 мест с `task_type IN ('disable','enable')`)
- **problem:** те же мёртвые task_type. `pending_disable_tasks`, `failed_tasks_24h`, History timeline/tasks-summary, `last_disable_at` в History→Ads → 0/пусто при реальных auto-stop.
- **impact:** 💸 наблюдаемость денег. Известный долг, но не закрыт.
- **fix:** переориентировать 8 запросов на `meta_api_mutation`+`mutation_kind`; лучше единый хелпер-предикат «канал отключения».
- **confidence:** high

### G0-3 [CRIT] Фронт: тогл «Канал авто-стопа» и кнопка «Перезапустить Disable Worker» → 404
- **where:** `frontend/src/lib/api/settings.ts:127-135` (`PATCH /settings/observer/act-via-api`), `:205-212` (`POST /disable-worker/restart`); UI — `components/dashboard/ScannerControls.tsx:56-174`, `components/settings/HealthTab.tsx`
- **problem:** оба эндпоинта физически удалены (Фазы 2/4, миграция 0016). Тогл/кнопка видимы оператору, при клике → 404/422.
- **impact:** UX-краш + дезориентация по работе авто-стопа (`act_via_api` фолбэк `?? true` всегда рисует «Marketing API» независимо от факта).
- **fix:** удалить секцию act-via-api из `ScannerControls.tsx`, кнопку из `HealthTab.tsx`, хуки из `settings.ts`, поле из `lib/types/api.ts`; регенерировать `api-generated.ts` (`npm run gen:api`).
- **confidence:** high ✓verified

### G0-4 [HIGH] mini-app: «Очередь отключений» пуста + те же мёртвые имена воркеров
- **where:** `frontend-mini/src/pages/DashboardPage.jsx` (disable-tasks), `HealthPage.jsx:11-12` (`disable_worker`/`enable_worker` в `WORKER_LABELS`)
- **problem:** mini-app ЖИВ (`run.sh:964-998` + Tailscale Funnel). Те же осколки: пустая очередь отключений (G0-1), лейблы удалённых воркеров.
- **impact:** 💸 наблюдаемость на проде-канале (mini — основной мобильный доступ).
- **fix:** см. G0-1 (бэк) + убрать строки 11-12 в HealthPage.
- **confidence:** high

### G0-5 [MID] Stale ссылки на disable/enable в проде-коде, доке и тестах
- **where:** `core/ai_assistant/diagnostics.py:43-46` (`_LOG_HINTS_BY_KEY` → disable_worker.log/enable_worker.log); `CLAUDE.md` (раздел воркеров + «DOM-парсинг» вместо am_tabular); `frontend/src/tests/settings.test.tsx:19` + `api-generated.ts` (act_via_api)
- **impact:** дебаг по мёртвым подсказкам; тест зелёный на удалённом функционале (ложная уверенность).
- **fix:** вычистить log-hints, обновить CLAUDE.md (по правилу — предложить diff, не править молча), убрать act_via_api из тестов/типов.
- **confidence:** high

---

## 🔴 CRIT — money-ядро (детект/мутации)

### C1. Снуженный/крашнувшийся ад залипает в `stop_sent` без авто-стопа → слив бюджета
- **where:** `core/observer/pipeline.py:320-323` + `:359-368` (`_suppress_emit` обнуляет `create_disable_task`) + `state_machine.py:121-129` + `writers.py` (4 независимых коммита вопреки docstring)
- **problem:** на `warning_sent→stop_sent` FSM просит `create_disable_task=True`, но снуз (`_suppress_emit`) обнуляет его, при этом `new_state=stop_sent` пишется безусловно. После снуза `stop_sent→stop_sent` задачу уже не создаёт никогда. Recovery-реконсилера нет. Два триггера: (1) снуз WARNING (обычное действие), (2) краш между транзакциями FSM↔outbox.
- **impact:** 💸 убыточный ад в STOP крутится без отключения, повторов нет.
- **fix:** recovery на `stop_sent→stop_sent` без активной pause-задачи (idempotency_key с поколением, т.к. token переиспользуется); ИЛИ в `_suppress_emit` глушить только `emit_alert`; объединить FSM-переход и создание задачи в одну транзакцию.
- **confidence:** high ✓verified построчно

### C2. Requeue необратимых мутаций после коммита Meta → дубль кампании, двойной бюджет
- **where:** `apps/meta_api_worker/main.py:359-365` (`TemporaryError→requeue`), `:366-383` (`ValueError→requeue`), `:384-389` (`Exception→requeue`)
- **problem:** `create_campaign`/`duplicate_campaign` при потере ответа (gRPC DEADLINE/битый JSON/circuit-open/ValueError на постобработке успешного ответа) уходят в requeue → повторное исполнение. `idempotency_key` (enqueue) от retry той же строки не спасает. `CreateCampaignPartialError` обработан верно, но «успех с потерянным ответом» — нет.
- **impact:** 💸 вторая реальная кампания/копия (низкочастотные ручные операции, но необратимо).
- **fix:** для необратимых kinds — pre-flight GET по имени перед повтором, либо `mark_failed`+алерт вместо blanket-requeue; разделить ошибки вызова и постобработки.
- **confidence:** high ✓verified

---

## 🟠 HIGH

### H1. Два reconciler'а `meta_api_mutation`; meta-local не бампает `attempt_count` (B2+B3)
- **where:** `core/meta_api/reconciler.py:24-52` (из `apps/meta_api_worker/main.py:409-413`) vs канонический `core/tasks/queue.py:305-335` (из `apps/reconciler_worker`)
- **problem:** оба активны, гонка; meta-local не инкрементит попытку → stuck-задача (worker крашнулся после отправки необратимой мутации) ретраится без расхода лимита → усиливает C2.
- **fix:** один владелец reconcile — убрать `reconcile_loop` из meta_api_worker либо добавить bump.
- **confidence:** high

### H2. `create_campaign` не enforce'ит верхний cap бюджета (асимметрия с `set_adset_budget`)
- **where:** `core/meta_api/mutations/create_campaign.py:568-573` (`_validate_cents` только `>0`); ср. `set_adset_budget.py:38-39` ($100k/$1M cap). Draft-tool `request_create_campaign.py:108,114` — cap только в schema-подсказке, в `run()` не enforced.
- **impact:** 💸 выгорание бюджета через ошибочное/hallucinated значение AI или прямой MCP-вызов — тот же риск, что закрыт для set_adset_budget, идёт мимо порога.
- **fix:** применить `MAX_DAILY/LIFETIME_BUDGET_CENTS` в `_validate_cents` + в `run()` draft-tool + regression-тест на reject.
- **confidence:** high

### H3. Fan-out spend в History→Campaigns: кампания дробится на N строк (B4+B5)
- **where:** `core/dashboard/history_queries.py:233` — `al.alerts_count` (per-ad COUNT) в `GROUP BY`
- **problem:** при ad'ах с разным числом алертов кампания расщепляется на строку на каждое значение → `SUM(spend)` дробится, `alerts_count` семантически неверен. Тест `test_api_history.py:460` маскирует (оба ad с alerts_count=1).
- **impact:** 💸 spend кампании занижен/раздроблен в History. Эталон рядом — `fetch_offers` делает корректно.
- **fix:** алерты в отдельную CTE с `GROUP BY campaign_id`, убрать из основного GROUP BY.
- **confidence:** high ✓verified

### H4. health_watchdog не следит за money-критичным `cabinet_scheduler`
- **where:** `apps/health_watchdog/main.py:47-49` (`DEFAULT_EXPECTED_WORKERS` — нет cabinet_scheduler/digest_scheduler/creator*); контраст — `apps/api/routers/v1/health_details.py:31-43` (полный список 11). `.env.example:87-89` — пустой `EXPECTED_WORKERS=` → `parse([])` → мониторинг НИКОГО.
- **impact:** 💸 зависший cabinet_scheduler (heartbeat-stall при живом процессе) пройдёт без TG-алерта → автостарт кабинета молча не сработает. Пустой `.env`-ключ глушит весь алертинг воркеров.
- **fix:** синхронизировать `DEFAULT_EXPECTED_WORKERS` с health_details (11 имён); починить `.env.example`.
- **confidence:** high

### H5. `crypto.py` без unit-тестов (rotate_encryption_key silently-skip)
- **where:** `core/crypto.py:132-231` (`rotate_encryption_key` raw SQL, `InvalidToken`→молчаливый skip), `:85-129` (`verify_encryption_key`); тестов нет
- **impact:** 🔐 тихая потеря секрета при ротации; нет гарантии round-trip encrypt/decrypt и fail-fast при подмене ключа. Класс «shape-тесты пропустили семантику».
- **fix:** unit-тесты round-trip / чужой ключ / mismatch / rotate с подсчётом нерасшифрованных (алертить, не скипать молча).
- **confidence:** high

### H6. mini DraftsPage: `formatPayload` использует несуществующие mutation_kind → подтверждение бюджета вслепую
- **where:** `frontend-mini/src/pages/DraftsPage.jsx:26,44,53` (`set_budget`/`clone_campaign`/`bulk_pause`) vs реальные `set_adset_budget`/`duplicate_campaign`/`bulk_status_change`
- **impact:** 💸 оператор подтверждает бюджетный/клон-драфт, видя сырой JSON вместо «$50/день» — money-риск подтверждения вслепую.
- **fix:** заменить ключи на реальные.
- **confidence:** high

### H7. Прочие HIGH (без money-слива, но ломают функции)
- **H7a [B4]** `_extract_client_key` доверяет любому `X-Forwarded-For` без trusted-proxy → обход rate-limit `/ai/analyze` → расход AI-бюджета. `ai_analyze.py:50-57`. Fix: ProxyHeadersMiddleware с trusted_hosts или вторичный ключ по `request.client.host`.
- **H7b [B4]** `BodySizeLimitMiddleware` (64KB) глобально режет multipart `/tools/creative-uniquify` (внутренний лимит 200MB) → 413. `middleware/body_size.py:34`. Fix: исключение по path-prefix `/api/tools/`. (dev-only функция).
- **H7c [F1]** Drafts approve зовёт `/disable-tasks/{id}/retry`, а бэк разрешает retry только failed/cancelled → PENDING-драфт всегда 409. `frontend/src/lib/api/drafts.ts:23,38-43`. Fix: фетчить `status=DRAFT` + approve через `approve_draft_task`.
- **H7d [F1]** `setState` в теле компонента `OfferFormModal.tsx:87-91` (вне useEffect) → двойной рендер/StrictMode-петля. Fix: useEffect/lazy-init.
- **H7e [F1]** Drafts-дедлайн считается в локальном времени (`setHours(getHours()+24)`), бэк в UTC → метка «истекает скоро» врёт. `routes/drafts/index.tsx:141-153`. Fix: `setUTCHours`.
- **H7f [F2]** mini DashboardPage `STATE_LABELS` неполный (нет DISABLED/NORMAL → raw-значения). `frontend-mini/src/pages/DashboardPage.jsx:13-17`.
- **H7g [B3]** `.env.example` слепота — см. H4.

---

## 🟡 MID (корректность / краевые / покрытие)

- **M1 [B1]** Включение frequency-anomaly незаметно меняет guardrail CPC/CPL/CPR (через `ctx.impressions`, `pipeline.py:99-110` + `evaluator.py:388`); коммент вводит в заблуждение. Fix: передавать impressions/reach безусловно.
- **M2 [B1]** Guardrail WARNING `>` вместо `>=` (`evaluator.py:409`) — теряется граничный WARNING.
- **M3 [B1]** Integration-тест маскирует C1 (`test_observer_db.py:414-439` ручной сброс state). Fix: тест полного снуз-флоу.
- **M4 [B2]** `idempotency_key` без salt: легитимная повторная мутация молча → no-op (`None`); callers не проверяют. `queue.py`.
- **M5 [B2]** audit `http_status=200` хардкод для batch — батч-фейл логируется как успех (`audit.py:188-203`).
- **M6 [B2]** `bulk_status_change` дублирует batch-сборку/парсинг вместо `_batch_helpers`.
- **M7 [B2, латентный]** bulk-pause в **полной** форме (`{object_ids,status:PAUSED}`) гейт `_is_activating_mutation` считает активирующим → откладывает на паузе. Сейчас не триггерится (единственный bulk-pause — `request_bulk_pause` в сокращённой форме). Fix: учитывать обе формы (переиспользовать `_resolve_bulk_ad_toggle`).
- **M8 [B3]** cabinet_scheduler ставит dedup-ключ ДО действия → транзиентная ошибка = пропуск автостарта на сутки (безопасное направление — ads off). Fix: ключ после `create_mutation_task`.
- **M9 [B4]** `enable_tasks` GET task_type='enable' (см. G0-2). + `import json` внутри хендлеров (`history.py:188`, `enable_recommendations.py:206`). + ai_analyze проверяет провайдера до rate-limit (enumeration).
- **M10 [F1]** `SpendChartCard.computeSummary` avg = total/бакеты → завышено для неполных периодов; `ObserverTab.tsx` 632 строки.
- **M11 [F2]** Дубли STATE_LABELS/форматтеров в mini (3 копии).
- **M12 [X]** Partition-pruning тест dispatcher без негатив-кейса (вне-окна); `created_by_chat_id` не покрыт unit-контрактом draft-tool→queue; AI diagnose шлёт хвост логов во внешние LLM без redaction.

---

## ⚪ LOW / tech-debt

- **frontend-legacy/ — МЁРТВ** (не в run.sh/compose/nginx). Кандидат на удаление целиком (god-components 1559/1570 строк, дубль api.js ×2, кнопка на мёртвый эндпоинт). **Рекомендация: удалить папку.**
- Файлы >500 строк: `apps/observer_worker/main.py` (806), `create_campaign.py` (631), `clients/python_grpc/client.py` (631), `evaluator.py` (627), `apps/api/routers/v1/tma.py` (623), `offers.py` (573), `enable_recommendation_worker/main.py` (550).
- B1: мёртвая ветвь `pipeline.py:267-275`.
- B2: magic-числа обрезки в audit; копипаста resolver'ов в ownership.py (6×).
- B3: observer httpx-клиент не закрывается на shutdown; digest dedup GET→SET не атомарен (митигировано worker_lock); enable_reco DB-insert до Redis-NX; telegram_poller shutdown-латентность 25с > stopwaitsecs 15.
- B4: копипаста JOIN-цепочек (×7).
- B5: chart-data «пила» по часам (кумулятив без дельты); ingest fk-resolve вне транзакции (микро-race, деньги не теряются); хардкод-список partitioned-таблиц в 2 местах (ловушка при добавлении 9-й).
- F1: CTR/Frequency через `parseFloat().toFixed` вместо форматтеров; `toNumber→0` для null spend; нет тестов форматтеров edge-cases.
- F2: mini — нет тестов money-путей.
- X: conftest не сбрасывает AI-синглтоны между тестами; `providers.py` кладёт `resp.text[:200]` в ошибку.

---

## Чистые зоны (проверено — ОК)
- **Money-агрегации (главный риск проекта) — ЧИСТО.** Наивного `SUM` по кумулятивным `ad_metrics` нет нигде: все потребители через `DISTINCT ON` (`metric_aggregation.py` эталон, history/performance/chart-data/offers/digest/snapshot). Единственное исключение — fan-out H3 (GROUP BY, не SUM-семантика).
- **adset_pro money:** aggregator absolute-recompute (не задваивает депозиты), ingest двухступенчатый дедуп, credentials Fernet+BYTEA ротация, outgoing `send()` не бросает — всё корректно.
- **Схема/индексы/partitioned/FK/migrations:** партиционные индексы и UNIQUE с partition-key на месте; FK CASCADE/SET NULL корректны; цепочка миграций линейна, head 0016, multiple-heads нет; 0016 симметрична.
- **batch-encode** JSONPath refs сохраняются (`create_campaign` работает); **outbox-гонки** (claim FOR UPDATE SKIP LOCKED + bool + WHERE status guard); **ACL** approve_draft_task не обойти; **FSM-guard** терминальных; **open_token** persistence; **set_adset_budget** cap; **owner-scoping** word-boundary; **rate-limit** без fail-open; **alert_dispatcher** pre-claim dedup; **graceful shutdown** + worker_lock; **heartbeat writers** (проблема в reader-списке H4, не в writer'ах).
- **frontend/** стек — TS strict (не JSX), ручного `as any` нет (только в авто-`routeTree.gen.ts`).

## Не покрыто глубоко
- `services/browser-agent/src` (115 TS-файлов) — затронут поверхностно в X (security парсера/мутаций); полноценный TS-аудит scanner/creator/session — отдельный прогон.
- Полная глубина F1 (90 TS/TSX) — основные страницы покрыты, не все компоненты.

---

## Рекомендованный план

**Этап 0 — доделать DOM-removal (наш регресс, быстро, не money-слив):**
1. G0-1+G0-2 — API-чтение disable/enable на `meta_api_mutation` (1 фильтр + retry/cancel + 8 counters). Единый хелпер-предикат.
2. G0-3+G0-4 — фронт: убрать тогл act_via_api и кнопку disable-worker (основной + mini HealthPage), регенерировать типы.
3. G0-5 — вычистить stale log-hints/тесты; предложить diff CLAUDE.md.

**Этап 1 — money-CRIT ядра:**
4. C1 (+M3 тест) — recovery залипшего stop_sent.
5. C2 + H1 — безопасный retry необратимых мутаций + единый reconciler.

**Этап 2 — money/security HIGH:**
6. H2 (create_campaign budget cap), H4 (health_watchdog + .env.example), H3 (fan-out spend), H5 (crypto тесты), H6 (mini formatPayload), M7 (bulk-форма — дёшево).
7. H7a (XFF rate-limit), H7c-H7e (фронт drafts), H7b (tools multipart).

**Этап 3 — корректность MID:** M1, M2, M4, M5, M6, M8, M9-M12.

**Этап 4 — tech-debt:** удалить `frontend-legacy/`; декомпозиция файлов >500; усиление тестов (партиционный негатив, money-границы, ACL-контракт).
