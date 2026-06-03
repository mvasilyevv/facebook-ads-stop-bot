# Backend Code-Quality & Architecture Audit — FB Stop Bot

**Дата:** 2026-05-28
**Метод:** независимый ручной review (grep + Read реального кода, без доверия отчётам спавнов). НЕ про покрытие тестами (см. `backend_test_audit_round_8.md`) — про качество реализации.
**Scope:** 12 воркеров + FastAPI (61 endpoint / 17 модулей) + core/ + Node gRPC обвязка. Исключено по запросу: `apps/api/routers/ws.py`, WS-регистрация в `main.py`, весь фронт.

---

## 1. Executive summary

Top-7 находок (по одной строке):

1. **CRIT — `ad_metrics.spend/leads/deposits` — кумулятивные snapshot'ы, а 8 аналитических endpoint'ов делают наивный `SUM()` по всем cycle-строкам → spend завышается в 10–100×.** Тихо ломает все денежные цифры на фронте.
2. **CRIT — `observer:runtime` контракт рассогласован: воркер пишет ключ `worker_status` со значениями `scanning/idle/paused`, а ВСЕ 3 читателя ждут ключ `status` со значениями `running/paused`.** `/observer/status` и `/dashboard/stats.observer_status` всегда возвращают `unknown`.
3. **HIGH — smoke-test заглушки в проде:** `validate-columns` всегда `valid:true`, `stop_rule_codes/warning_rule_codes` захардкожены `[]` — фронт тихо получает фейк.
4. **HIGH — `/ai/analyze` rate-limit — in-memory per-process** (не Redis), при k8s-репликах лимит 20/час превращается в 20×N/час; за reverse-proxy все клиенты в одном bucket.
5. **MID — retry/cancel задач (`disable_tasks`/`enable_tasks`) не проверяют `rowcount`** после guarded UPDATE → при гонке отдают 200/204 как успех, хотя ничего не изменилось.
6. **MID — массивное копипасто между 6 router'ами:** JOIN-цепочка `FbAd→FbAdset→FbCampaign→Offer`, `_task_row_to_out`, разворот CSV-статусов, partition-window — 4 копии каждого.
7. **MID — `create_campaign` Batch API не транзакционен:** при частичном успехе (campaign+adset созданы, ad упал) → `mark_failed` без cleanup → осиротевшие PAUSED-объекты в Meta.

**Verdict: `needs-cleanup-round`.**
Костяк (FSM, outbox, ACL draft-approve, batch JSONPath-encode, partition-фильтры в горячем пути, timezone-гигиена, graceful shutdown воркеров) реализован **по-настоящему качественно** — security-фиксы Round 6/9 не косметика, они настоящие. Но #1 и #2 — это не техдолг, а **тихие баги корректности данных**, которые делают всю аналитическую витрину (DashboardPage performance/charts, HistoryPage, offers/compare) недостоверной. Один целевой раунд закрывает оба + копипасту. До этого аналитике на бэке доверять нельзя.

---

## 2. CRITICAL

### CRIT-1. Наивный `SUM()` по кумулятивным snapshot-метрикам → завышение spend/leads/deposits в десятки раз

**Суть.** `ad_metrics` — это **time-series snapshot'ов**: каждый scan-цикл (интервал ~90с) пишет новую строку с **текущим кумулятивным значением** метрики из Ads Manager («Amount spent» — накопительная сумма за выбранный период). Это зафиксировано в самой модели:

`core/models/observer/ad_metrics.py:31-33`:
> «Метрика объявления в конкретный момент scan'а. **Текущее значение = LIMIT 1 ORDER BY cycle_ts DESC.**»

Канонический агрегатор `core/telegram/digest_builder.py:158-218` делает это **правильно** — берёт per-ad последний snapshot через `DISTINCT ON (m.ad_id) ORDER BY m.cycle_ts DESC`, и только потом `SUM`. Комментарий прямым текстом: «на ad_metrics берётся самая поздняя строка в окне … суммарный spend = SUM(этих последних snapshot'ов)».

**А вот эти endpoint'ы суммируют ВСЕ строки в окне (каждый цикл!):**

| Файл:строка | Endpoint | Что считает неверно |
|---|---|---|
| `apps/api/routers/v1/history.py:131` | `/history/summary` | `SUM(spend)` |
| `apps/api/routers/v1/history.py:350-353` | `/history/campaigns` | `SUM(spend/leads/regs/deposits)` |
| `apps/api/routers/v1/history.py:515-518` | `/history/offers` | `SUM(spend/leads/regs/deposits)` |
| `apps/api/routers/v1/history.py:611-613` | `/history/ads` | `SUM(spend/leads/deposits)` |
| `apps/api/routers/v1/dashboard_performance.py:69-74` | `/dashboard/performance` top_campaigns | `SUM(spend/leads)`, и `cost_per_lead` поверх |
| `apps/api/routers/v1/dashboard_performance.py:119-122` | `/dashboard/performance` offer_leaderboard | `SUM(...)` |
| `apps/api/routers/v1/dashboard_timeseries.py:145-150` | `/dashboard/chart-data` | `SUM(spend) GROUP BY date_trunc('hour', cycle_ts)` — главный график дашборда! |
| `apps/api/routers/v1/offers.py:108-113` | `/offers/compare` | `SUM(spend/leads/regs/deposits)` |

**Масштаб искажения.** Если объявление сканируется ~40 раз/час и кумулятивный spend растёт от $0 до $50 за день, `SUM()` по дню вернёт сумму всех 40×24 snapshot'ов = тысячи долларов вместо $50. `/chart-data` (hour-bucket) даёт «зубчатую пилу» из ~40 сложенных кумулятивов в каждом часе.

**Почему это CRIT, а не HIGH.** Это не падение и не 500 — фронт получает «правдоподобные» числа, которые завышены на порядок-два. Пользователь принимает решения о бюджете/отключении по фейковым CPL/spend. Тихий money-bug — худший класс.

**Правильный фикс.** Везде заменить `SUM(metric)` на паттерн digest'а:
```sql
WITH last_metrics AS (
  SELECT DISTINCT ON (m.ad_id) m.ad_id, m.spend, m.leads, ...
  FROM ad_metrics m
  WHERE m.cycle_ts >= :from AND m.cycle_ts < :to
  ORDER BY m.ad_id, m.cycle_ts DESC
)
SELECT SUM(spend), SUM(leads) FROM last_metrics JOIN ...
```
**Нюанс для многодневных окон** (`/history/*` до 90 дней, `/offers/compare` до 90): даже digest-паттерн «per-ad latest в окне» берёт только финальный кумулятив на конец окна — если кабинет сбрасывается посуточно (cabinet_day), за 30 дней корректнее `DISTINCT ON (ad_id, date_trunc('day', cycle_ts))` → latest-per-day → SUM. Минимально-правильно — вынести единый helper `core/dashboard/metric_aggregation.py` и переиспользовать (см. §7). `build_ad_snapshot` (LATERAL LIMIT 1) и `ads_timeline` (сырые точки) — **корректны**, их не трогать.

---

### CRIT-2. Контракт `observer:runtime` рассогласован между писателем и всеми читателями

**Писатель** — `apps/observer_worker/main.py:152-160` (`_publish_runtime_status`):
```python
payload = {"worker_status": status, "active_phase": ..., ...}   # ключ worker_status!
```
со значениями `status ∈ {"scanning","idle","paused"}` (строки 210/215/229/241/270).

**Читатели** (все три ждут другой ключ и другие значения):
- `apps/api/routers/v1/dashboard_stats.py:61`: `status = payload.get("status")` → потом `if status in {"running","paused"}` иначе `unknown`. Ключа `status` нет → **всегда `unknown`**. Плюс даже будь ключ — значения `scanning/idle` не входят в `{running,paused}`.
- `apps/api/routers/v1/observer.py:76`: `payload.get("status", "unknown")` → `/observer/status` **всегда `unknown`**, а `worker_status`/`active_phase`/`next_scan_at` улетают в безымянный `extra`.
- `apps/api/routers/v1/health_details.py:167-175`: кладёт сырой JSON в `observer_runtime` — тут ОК, т.к. отдаёт as-is.

**Эффект.** Карточка «Observer: running/paused» на DashboardPage и весь `/observer/status` показывают `unknown` всегда, независимо от реального состояния воркера. Health-watchdog отдельно проверяет freshness ключа (это работает), но статусное поле — мёртвое.

**Фикс.** Договориться об одном контракте. Минимально — писатель пишет и `status`, и нормализует `scanning/idle → running`. Либо читатели читают `worker_status` и мапят `{scanning,idle}→running`. Добавить контрактный тест writer↔reader (его нет — поэтому рассогласование прожило все раунды).

**Сопутствующее (LOW):** docstring'и `settings_observer.py:138`, `observer.py:260,270` пишут «Subscriber в worker'е НЕ реализован — TODO» — **устарели**: observer теперь подписан на `fb_agent:observer:trigger` (`main.py:362-383`). Сигнал scan-now реально работает. Стоит обновить, иначе вводит в заблуждение.

---

## 3. HIGH

### HIGH-1. Smoke-test заглушки выдают фронту фейковый success (разбор в §6)
`settings_vision.py:47-72` (`validate-columns`/`save`/`apply-column-widths` → всегда `valid:true`/`noop`) и `core/dashboard/snapshot.py:122-123` (`stop_rule_codes/warning_rule_codes: []`). Детальный разбор и рекомендация — секция 6.

### HIGH-2. `/ai/analyze` rate-limit — process-local, не распределённый
`apps/api/routers/v1/ai_analyze.py:35-42,105` — `_analyze_rate_limiter` это **module-global in-memory** `_RateLimiter(max_per_hour=20)`. Кэш ответов — в Redis (правильно), но **счётчик лимита — в памяти процесса**.

Последствия при заявленном в CLAUDE.md/Helm горизонтальном масштабировании:
- N uvicorn-воркеров/реплик → фактический лимит **20×N/час**, не 20.
- `request.client.host` за k8s-ingress = IP прокси → все юзеры в один bucket (20/час на всех) ЛИБО, если форвардинг есть, без разбора `X-Forwarded-For` всё равно неверно.
- При рестарте процесса счётчик обнуляется.

CLAUDE.md заявляет «rate-limiter 20/hour per remote IP **поверх** ChatSession'овского 30/hour» как готовую защиту — фактически это per-replica и легко обходится. **Фикс:** Redis-backed sliding window (как уже сделано для AI-tools в `core/ai_assistant/tools/_ratelimit.py` — там Redis + in-memory secondary cap; переиспользовать тот же подход), + честный парсинг `X-Forwarded-For`.

### HIGH-3. `create_campaign` Batch API не транзакционен → осиротевшие объекты при частичном фейле
`core/meta_api/mutations/create_campaign.py:194-207` — если batch создал campaign+adset+creative, но `ad` упал, поднимается `ValueError` → в worker'е (`apps/meta_api_worker/main.py:104-111`) `ValueError ∈ _PERMANENT_EXCEPTIONS` → `mark_failed`. **Cleanup созданных объектов отсутствует** (только `ids_so_far` в тексте ошибки). В Meta остаётся висячий PAUSED-campaign без ad. Само кодирование JSONPath-refs (CRIT #1 Round 6) — **реально корректно** (`_batch_helpers._encode_value` сохраняет `{}:$.=`, проверено). Проблема в обработке partial-failure. **Фикс:** при частичном успехе — либо best-effort compensating DELETE созданных id, либо явно вернуть их в `result`/алерт оператору, чтобы он удалил вручную; задокументировать non-atomicity Batch API.

---

## 4. MID

### MID-1. retry/cancel задач не проверяют `rowcount` после guarded UPDATE (race-окно тихо проглатывается)
`apps/api/routers/v1/disable_tasks.py`:
- `retry` (236-315): SELECT статуса на одном connection, затем отдельным `engine.begin()` — `UPDATE ... WHERE status = ANY(:allowed)`. Guard защищает данные, но `rowcount` не проверяется. Если между SELECT и UPDATE worker заклеймил задачу (`failed→running`), UPDATE применится к 0 строк, а endpoint вернёт 200 с (фактически `running`) строкой как будто retry прошёл.
- `cancel` (321-362): тот же паттерн — `UPDATE ... WHERE status NOT IN (...)` без проверки rowcount → возможен 204 при фактическом no-op.

Транзакционная **целостность** не нарушена (guard в WHERE), но **ответ вводит в заблуждение** и UX рассинхронизируется. Аналогично — потенциально в `enable_tasks` (там сейчас только GET, но при добавлении write — тот же паттерн скопируется). **Фикс:** проверять `result.rowcount`; 0 → 409 «состояние изменилось, повторите».

### MID-2. `_PERMANENT_EXCEPTIONS` включает голый `ValueError` → транзиентные баги маскируются под permanent
`apps/meta_api_worker/main.py:104-111` — `ValueError` в списке permanent. Это покрывает валидацию payload в handler'ах (правильно), НО любой случайный `ValueError` из-за бага в коде/неожиданной формы Graph-ответа тоже → `mark_failed` без retry. Граница «permanent valid error» vs «случайный ValueError» размыта. **Фикс:** ввести доменный `MutationValidationError(ValueError)` и ловить его, а не базовый `ValueError`.

### MID-3. Массовое дублирование между router'ами (детали — §7)
JOIN-цепочка `FbAd→FbAdset→FbCampaign→Offer` повторяется ~7 раз дословно; `_task_row_to_out` — 4 копии; разворот CSV-статусов (`PENDING→[draft,pending]`) — 2 дословные копии; `_decimal_str`/`_int_or_none` — продублированы в 3 файлах; alert_events→ads JOIN + сборка dict (`triggered_by_rule_codes: None`) — в `dashboard.py` и `dashboard_stats.py` дословно.

### MID-4. Файлы > 500 строк в новом коде (нарушение design-rule CLAUDE.md)
- `apps/api/routers/v1/history.py` — **692** (god-router, 6 endpoint'ов с инлайн-SQL).
- `core/meta_api/mutations/create_campaign.py` — **524**.
- `apps/enable_recommendation_worker/main.py` — **519**.
- `apps/observer_worker/main.py` — **512**.
- (`core/rules/evaluator.py` 626, `core/campaign_recorder/event_injector.py` 675 — core-домен/Vision, частично pre-existing.)

### MID-5. `OfferOut` поля затипизированы как литерал `None` (не `str | None`)
`apps/api/routers/v1/schemas/offers.py:34,38,39` — `country_code: None = None`, `use_vision_creator: None = None`, `notes: None = None`. Поле физически **не может принять значение** — это «замораживает» null-контракт на уровне типа. Работает, но это антипаттерн (тип `None` вместо `Optional[...]`); при возврате полей в ORM потребуется правка схемы, а не только router'а. Лучше `str | None = None` с явным комментарием.

### MID-6. `ads_timeline` не валидирует `from_dt > to_dt`
`apps/api/routers/v1/ads_timeline.py:53-60` — в отличие от `dashboard.py:187` и `history.py:85`, нет проверки `from > to`. При перевёрнутом окне вернёт пустоту вместо 422. Непоследовательность контракта валидации между endpoint'ами.

---

## 5. LOW

- **`enable_recommendations.py:196`** — `import json as _json` внутри функции при том что модуль и так на инлайн-SQL; локальный импорт ради alias, мелочь.
- **`# type: ignore[arg-type]`** в `settings_vision.py:179-233` (6 шт.) — из-за `dict[str,object]` runtime-словаря, отдаваемого в типизированную схему; обоснованно, но лучше TypedDict.
- **`upload.py`** — 6× `# type: ignore[attr-defined]` на доступ к `self._client._stub` (приватный атрибут чужого класса). Работает, но хрупко: при рефакторе `MetaApiClient._stub` молча сломается. Лучше публичный аксессор.
- **`offers.py:254-255`** — детект unique-конфликта через `str(exc).lower()` поиск `"unique"/"duplicate"` вместо проверки `exc.orig`/SQLSTATE `23505`. Хрупко к локализации/версии драйвера.
- **Магические числа** разбросаны без констант: `make_interval` окна, `7 days` lookback в нескольких местах (есть `_METRICS_LOOKBACK_DAYS` в snapshot, но в offers.py `INTERVAL '7 days'` инлайн-строкой, дублирует семантику).
- **`dashboard_performance.py` docstring** обещает `cost_per_lead = SUM/NULLIF(...)`, код использует `CASE WHEN =0 THEN NULL` — эквивалентно, но doc≠code.

---

## 6. Smoke-test заплатки (разбор 3 костылей)

### (а) `core/dashboard/snapshot.py:120-125` — `stop_rule_codes:[]` / `warning_rule_codes:[]` захардкожены
**Критичность: HIGH (тихо ломает данные).** Фронт (AdsPage/Incidents) показывает «какие правила сработали» — а получает всегда пустой массив. Реальные коды лежат в `alert_events.matched_rule_codes` последнего события для ad'а. Сейчас они НЕ вытаскиваются → колонка/бейдж «сработавшие правила» на фронте всегда пуст, даже когда ad в `stop_sent`.
**Вердикт: ДОДЕЛАТЬ.** Добавить в `_build_sql` LATERAL по `alert_events` (с partition-фильтром `created_at >= NOW() - INTERVAL '7 days'`):
```sql
LEFT JOIN LATERAL (
  SELECT ae.matched_rule_codes, ae.stage
  FROM alert_events ae
  WHERE ae.ad_id = fb_ads.id AND ae.created_at >= NOW() - make_interval(days => :lookback_days)
  ORDER BY ae.created_at DESC LIMIT 1
) last_ev ON true
```
и маппить `stage='stop'→stop_rule_codes`, иначе `warning_rule_codes`. Это та же LATERAL-техника, что уже используется для метрик в этом же файле — стоит дёшево, partition-pruning сохраняется.

### (б) `apps/api/routers/v1/settings_vision.py:47-72` — `validate-columns` всегда `{valid:true}`, `save/apply-column-widths` всегда `noop`
**Критичность: HIGH в проде, MID сейчас.** Фронт думает «колонки Ads Manager на месте», хотя проверки нет. Если в Ads Manager пропали нужные колонки (реальный сценарий — Meta меняет UI), скан молча вернёт пустые метрики, а фронт покажет «всё ок». То есть заглушка **скрывает реальный operational-симптом**.
**Вердикт: УЗАКОНИТЬ-ИЛИ-ДОДЕЛАТЬ.** Краткосрочно — честно вернуть `501 Not Implemented` (как сделано для `/vision/profiles:311`), чтобы фронт показал «проверка недоступна», а не ложный зелёный. Правильно — gRPC-метод `ValidateColumns` в scanner.proto (browser-agent уже умеет `ValidateColumns` по CLAUDE.md — «25+ методов: … ValidateColumns»!), значит надо просто прокинуть существующий RPC, а не писать заглушку. **Стоит проверить — возможно RPC уже есть и заглушка вообще не нужна.** save/apply-column-widths (если ширины колонок нигде не персистятся) — можно узаконить как `noop` с честным `{persisted:false}`.

### (в) `apps/api/routers/v1/dashboard.py:170,278` — `stage.lower()` нормализация UPPERCASE от фронта
**Критичность: LOW (симптом, не баг).** Сам `.lower()` корректен и безопасен. Но это **симптом более глубокого рассогласования**: фронт исторически оперирует UPPERCASE (`WARNING/STOP`, `PENDING/...`), БД — lowercase. Сейчас это лечится точечно: `stage.lower()` тут, `status_mapper` для задач, `alert_state` в `_VALID_ALERT_STATES` lowercase там. Разнобой: где-то мапим, где-то `.lower()`, где-то ждём ровно lowercase.
**Вердикт: УЗАКОНИТЬ, но систематизировать.** `.lower()` оставить. Но завести один слой нормализации enum'ов фронт↔БД (по аналогии со `status_mapper.py`, который сделан хорошо) и применять единообразно, чтобы новый endpoint не забыл `.lower()` и не дал 422 на валидном вводе.

---

## 7. Дублирование (таблица: где-копипаста → куда-вынести)

| Паттерн | Где дублируется (файлы) | Куда вынести |
|---|---|---|
| JOIN `FbAd→FbAdset→FbCampaign→Offer` (LEFT/INNER) | `core/dashboard/snapshot.py`, `dashboard.py`, `dashboard_stats.py` (×3 подзапроса), `dashboard_performance.py` (×2 CTE), `history.py` (×4), `offers.py` (×2), `enable_recommendations.py`, `ads_timeline.py` | helper-фрагмент/CTE `core/dashboard/joins.py` или SQLAlchemy reusable `select` |
| `_task_row_to_out(row)` (id/status/next_attempt_at/last_error_message маппинг) | `disable_tasks.py:41`, `enable_tasks.py:29`, `enable_recommendations.py` (`_rec_row_to_out` похож), `dashboard_stats.py:_query_recent_disable_tasks` | `apps/api/utils/task_serializer.py::task_row_to_out` |
| Разворот CSV-статусов фронта (`PENDING→[draft,pending]` + `from_frontend_task_status`) | `disable_tasks.py:84-97`, `enable_tasks.py:72-84` (дословно) | `apps/api/utils/status_mapper.py::expand_frontend_statuses_csv` |
| **Агрегация метрик из `ad_metrics`** (правильный DISTINCT ON pattern) | сейчас правильно ТОЛЬКО в `digest_builder.py`; неправильно — в 8 endpoint'ах (CRIT-1) | `core/dashboard/metric_aggregation.py::aggregate_latest_per_ad(...)` — **единый источник, чинит CRIT-1** |
| `_decimal_str` / `_int_or_none` | `dashboard_performance.py:43`, `dashboard_timeseries.py`, (`snapshot.py::_decimal_to_str`) | `apps/api/utils/serialize.py` |
| alert_events→ad JOIN + сборка dict с `triggered_by_rule_codes:None` | `dashboard.py:211-255`, `dashboard_stats.py:269-308` (дословно) | общий `_alert_event_row_to_out` |
| Чтение `observer:runtime` из Redis (try/json/fallback) | `dashboard_stats.py:41-64`, `observer.py:54-95`, `health_details.py:166-176` | `core/observer/runtime.py::read_observer_runtime` (+ заодно фикс CRIT-2 в одном месте) |
| `NULL::text AS ad_name` + повторное чтение task после INSERT | `disable_tasks.py:206-229`, `enable_recommendations.py:240-275` | общий `_read_task_for_response` |

---

## 8. Что реально хорошо (честно)

Не только негатив — ряд мест сделан на совесть:

1. **Draft-approve ACL (`core/meta_api/queue.py::approve_draft_task`)** — образцово. `is_admin_recipient` верифицируется ВНУТРИ функции, owner-chat_id enforced через SQL `WHERE created_by_chat_id=:ccid`, `rowcount` проверяется, три ветки (owner / verified-admin / MCP-null-draft) явно разделены. CRIT #2/#6 Round 6 — настоящий фикс, не галочка.
2. **`_batch_helpers._encode_value`** — нетривиальный кастомный form-encoder, который сохраняет JSONPath-refs `{result=campaign:$.id}` нетронутыми и кодирует только form-разделители. CRIT #1 Round 6 реально работает (проверено побайтово).
3. **alert_dispatcher pre-claim паттерн** (`INSERT ... ON CONFLICT DO NOTHING RETURNING` с sentinel `message_id=0`, DELETE-rollback при ошибке отправки) + partition-фильтр `created_at >= NOW()-1h` (CRIT #1 Round 8) — корректный anti-dup для TG.
4. **Graceful shutdown воркеров** (`toggle_executor.run_toggle_loop` + observer `main_loop`) — `stop_event` проверяется ПЕРЕД claim, in-flight задача не теряется, listener_task корректно отменяется в `finally`. Race pubsub↔main отсутствует (state-флаги + Event).
5. **Timezone-гигиена** — ноль `datetime.utcnow()`/naive `datetime.now()` во всём core+apps. Везде `now(UTC)`.
6. **Marketing API изоляция** — ни одного прямого httpx к `graph.facebook.com`; всё через gRPC `ExecuteGraphCall`. Design-rule соблюдён строго.
7. **`mark_succeeded/mark_failed → bool` + `WHERE status='running'` guard** + проверка `applied` во всех воркерах (toggle/meta_api) — защита от double-execution при reconciler-race реализована последовательно.
8. **enable_recommendations promote** (`enable_recommendations.py:153-237`) — INSERT task + UPDATE `promoted_to_task_id` в **одной** `engine.begin()` транзакции с `SELECT ... FOR UPDATE`. Атомарность есть, race закрыт. (Это образец того, как НЕ сделаны retry/cancel из MID-1.)
9. **Partition-pruning в горячем пути** — observer pipeline, digest, snapshot LATERAL, alert_dispatcher, все `/history` BETWEEN, `compare` — везде partition-key в WHERE. Round 8 нашёл единственный full-scan в alert_dispatcher → Round 9 починил; **новых full-scan'ов не найдено**.
10. **`asyncio.gather` политики осознанные:** `/dashboard/stats` и `/performance` — fail-all (секция цельная), `/dashboard/batch` — partial-failure через `_safe_call`-обёртку. Консистентно с задокументированным UX. `_safe_call` ловит broad Exception, но логирует (не silent).

---

## 9. Verdict + prioritized fix list

**Verdict: `needs-cleanup-round`** — один целевой раунд. Архитектура и security здоровы; основная боль — две тихие data-correctness ошибки + копипаста.

| # | Severity | Fix | Файлы |
|---|---|---|---|
| 1 | **CRIT** | Вынести `aggregate_latest_per_ad` (DISTINCT ON pattern) и заменить 8 наивных `SUM()` | history.py, dashboard_performance.py, dashboard_timeseries.py, offers.py |
| 2 | **CRIT** | Согласовать контракт `observer:runtime` (ключ+значения) writer↔reader + контрактный тест | observer_worker/main.py, dashboard_stats.py, observer.py |
| 3 | **HIGH** | `snapshot.py`: LATERAL по alert_events → реальные stop/warning_rule_codes | core/dashboard/snapshot.py |
| 4 | **HIGH** | `validate-columns`: проверить готовый gRPC `ValidateColumns`, прокинуть его (или честный 501) | settings_vision.py |
| 5 | **HIGH** | `/ai/analyze` rate-limit → Redis sliding-window + X-Forwarded-For | ai_analyze.py (переиспользовать tools/_ratelimit.py) |
| 6 | **HIGH** | `create_campaign`: compensating cleanup / явный возврат осиротевших id при partial fail | mutations/create_campaign.py |
| 7 | **MID** | retry/cancel: проверять `rowcount`, 0→409 | disable_tasks.py (+enable при write) |
| 8 | **MID** | `MutationValidationError(ValueError)` вместо голого ValueError в permanent | meta_api_worker/main.py, mutations/*.py |
| 9 | **MID** | Вынести `_task_row_to_out`, CSV-status-expand, observer-runtime-reader, alert-row-out (см. §7) | apps/api/utils/* |
| 10 | **MID** | Разнести `history.py` (692) по 6 файлам/endpoint'ам | history.py |
| 11 | **MID** | `OfferOut`: `None`-тип → `str|None`+коммент | schemas/offers.py |
| 12 | **MID** | `ads_timeline`: добавить `from>to` → 422 | ads_timeline.py |
| 13 | **LOW** | Обновить устаревшие «subscriber не реализован» docstring'и | settings_observer.py, observer.py |
| 14 | **LOW** | unique-конфликт через SQLSTATE 23505, не `str(exc)` | offers.py |
| 15 | **LOW** | `upload.py`: публичный аксессор вместо `_stub` + `# type: ignore` ×6 | core/meta_api/upload.py, client.py |
| 16 | **LOW** | Привести `cost_per_lead` doc к коду в dashboard_performance | dashboard_performance.py |
| 17 | **LOW** | Завести единый enum-нормализатор фронт↔БД (узаконить `.lower()`) | apps/api/utils/ |

**Главный месседж:** бэк **не «схалтурен в целом»** — сложные критичные узлы (ACL, batch-encode, FSM-guards, partition-фильтры, shutdown) сделаны по-настоящему. Но `SUM(cumulative_spend)` и мёртвый `observer_status` — это тихие баги, прошедшие сквозь все раунды и 974 теста, потому что тесты проверяли «не падает / shape совпадает», а не «число семантически верное» и «writer-ключ == reader-ключ». До их фикса аналитической витрине доверять нельзя.
