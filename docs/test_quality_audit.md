# Test Quality Audit — FB Stop Bot

**Дата:** 2026-05-28
**Метод:** независимый ручной review тестового кода (grep по слабым паттернам → Read подозрительных файлов). Фокус: «почему 1028 зелёных тестов пропустили 2 CRIT money-bug'а» и поиск всех остальных фейк-зелёных того же класса.
**Не путать с:** `backend_test_audit_round_8.md` (что НЕ покрыто) и `backend_code_quality_audit.md` (качество прод-кода). Этот отчёт — про **качество самих тестов**: проверяют ли они ЗНАЧЕНИЕ, а не только форму.
**Suite:** 1024 теста (533 unit + 491 integration). Round 10 уже добавил 3 эталонных файла-фикса (40 тестов) — они изучены как образец.

---

## 1. Executive summary

### Корневая причина пропуска обоих CRIT — ОДНА: тесты проверяли форму ответа на данных, неспособных отличить баг от корректности.

Оба CRIT прошли сквозь все раунды не из-за слабости фреймворка и не из-за отсутствия тестов на endpoint'ы — endpoint'ы были покрыты. Причина в **дизайне тест-данных и ассертов**:

1. **`SUM(cumulative_spend)` (8 endpoint'ов, завышение ×10–100).** Тесты `test_api_history.py`, `test_api_dashboard_performance.py`, `test_api_dashboard_chart_data.py` сеяли **ровно ОДИН `ad_metrics`-snapshot на объявление**. При одном цикле `SUM(spend)` и корректный `latest-per-ad` дают **идентичное число** — баг физически невидим. Вдобавок ассерты были **lower-bound** (`spend >= 300`, `spend > 0`, `leads >= 40`), которые проходят даже при завышении в 100×. Чтобы поймать баг, тест обязан был вставить ≥2 кумулятивных цикла (10→20) и сверить `== 20` (latest), а не `>= 10`. Именно это делает канонический `test_digest_builder.py` (поэтому `digest_builder` — единственный агрегатор без бага: его ТЕСТ тоже был правильным) и новый Round 10 `test_metric_aggregation_semantics.py`.

2. **`observer:runtime` writer пишет `worker_status`, reader читает `status` (всегда `unknown`).** Тест writer'а (`test_observer_worker_loop.py`) и тесты reader'ов (`test_api_dashboard_stats.py`, `test_api_observer.py`) существовали **порознь, каждый со своим mock'ом**. `dashboard_stats` тест даже ассертил `observer_status == "unknown"` (line 114) — то есть **фиксировал сломанное поведение как ожидаемое**. Не было **контрактного теста** writer→reader (один пишет реальным кодом, другой читает реальным кодом, сверяем что значение долетело). Round 10 закрыл `test_observer_runtime_contract.py`.

**Общий знаменатель:** оба класса — «две стороны проверяются изолированно на данных, которые не нагружают границу». Money-bug — граница «один цикл vs много циклов». Status-bug — граница «writer-ключ vs reader-ключ».

### Top-7 категорий слабых тестов (по убыванию цены)

| # | Категория | Где | Severity |
|---|---|---|---|
| 1 | **Money-агрегации со single-cycle фикстурой + `>=` ассертом** (тот же класс что CRIT-1, НЕ перекрытые Round 10) | `test_api_dashboard_chart_data.py`, `test_api_dashboard_spend_history.py`, `test_api_history.py` (summary/campaigns/offers/ads), `test_api_dashboard_performance.py` | **CRIT** |
| 2 | **Партиционные тесты-обманки** — «не упало», но не проверено что данные ВНЕ окна исключены | `test_api_dashboard_chart_data.py::test_chart_partition_pruning`, `test_api_history.py::test_partitioned_query_timing` | **HIGH** |
| 3 | **Counter-семантика на `>=`** — двойной счёт (JOIN fan-out) не ловится | `test_api_dashboard_stats.py` (counts по alert_state, pending/failed tasks) | **HIGH** |
| 4 | **Heartbeat writer↔reader контракт** — watchdog `EXPECTED_WORKERS` имена ≠ реальные `WORKER_NAME` writer'ов | `apps/health_watchdog` vs воркеры; нет контрактного теста | **HIGH** |
| 5 | **Pubsub subscriber-wiring тавтология** — тест переопределяет handler локально, не проверяет что worker связал канал↔handler | `test_worker_subscribers.py` | **MID** |
| 6 | **Shape-only `status_code == 200` + слабые ассерты** (`is not None`, `len > 0`, substring) | широко: 182× `==200`, 140× `is not None`, 154× `len(` | **MID** |
| 7 | **«Integration», стабящие БД** (named integration, реально не интегрируют) | `test_api_postback_security.py` (стабит `ingest_postback`) | **LOW** |

### Verdict: **точечно слаб в денежно-агрегационной зоне; системно здоров в остальном.**

Suite **не «фейковый в целом»**. Контрактные тесты ядра реальны и сильны: `test_meta_api_mutations.py` (инспектит `call_args.kwargs` — точный Graph-payload), `test_meta_api_adapters.py` (field-by-field + вычисляемые `cost_per_lead == 20/4`), `test_toggle_worker_db.py` (payload→gate→DB сквозной контракт), `test_status_mapper.py` (round-trip enum), `test_digest_builder.py` (мультицикловая семантика), `test_rules_evaluator_property.py` (Hypothesis, нашёл реальный баг в Round 8). **Та же слепая зона, что родила оба CRIT, прикрыта Round 10 лишь ЧАСТИЧНО** — добавлены новые семантические/контрактные файлы, но старые shape-only тесты в `chart-data`/`spend-history`/`history` (summary, campaigns, offers, ads, performance) **остались зелёными-обманками** рядом с новыми правильными. Нужен **один целевой test-hardening раунд** (~0.5–1 раунда): усилить ~12 money/counter ассертов до exact-value на мультицикловых данных + добавить 1 heartbeat-контракт. Это закрывает весь класс.

---

## 2. Категории A–F: найденные тесты

### A. Shape-only тесты на семантических данных

#### A-1. [CRIT] `test_api_dashboard_chart_data.py` — НЕ проверяет spend ни в одном тесте

Все 6 тестов проверяют только **количество бакетов**, никогда — значение `spend` бакета:
- `test_chart_bucket_hour` (line 90): вставляет 2 метрики → `assert len(buckets) >= 1`.
- `test_chart_bucket_day` (line 110): вставляет **3 кумулятивных цикла в один день** (идеальная заготовка для проверки CRIT-1!) → ассертит только `len(buckets) >= 1`. **Если бы здесь стояло `assert buckets[0]["spend"] == latest`, баг был бы пойман.**
- `test_chart_24h_hour_max_buckets`, `test_chart_no_phantom_buckets`, `test_chart_active_ads_distinct` — счётчики/active_ads, не spend.

`/chart-data` — главный график дашборда. Это **первый эпицентр CRIT-1**, и он до сих пор не имеет семантического spend-ассерта (Round 10 проверил chart-data только в отдельном `test_metric_aggregation_semantics.py` через scoped-SQL, но НЕ усилил сам `test_api_dashboard_chart_data.py`).
**Не проверяется:** spend-значение бакета = latest-per-(hour×ad), не SUM всех циклов.

#### A-2. [CRIT] `test_api_dashboard_spend_history.py` — НЕ проверяет spend ни в одном тесте

Все 5 тестов ассертят только `{p["fb_ad_id"] for p in points}` (membership) — ни одного ассерта на `p["spend"]`. spend-history возвращает сырые точки (по коду — корректно, не агрегирует), поэтому риск ниже, но контракт «точка несёт верный spend» не зафиксирован вообще.
**Не проверяется:** значение `spend`/`cycle_ts` в возвращаемых точках.

#### A-3. [CRIT] `test_api_history.py` — single-cycle + lower-bound на 4 из 6 endpoint'ов

- `test_summary_happy_path` (line 200): 1 snapshot на ad (200.50 / 100.00) → `assert spend >= 300.0`, `leads >= 40`, `deposits >= 7`. **`>=` + single-cycle** = слепо к завышению.
- `test_summary_default_30_days` (line 219): `assert float(...spend) > 0` — чистый smoke.
- `test_campaigns_group_and_sort` (line 385): сортировку проверяет, точное `spend` кампании — нет.
- `test_offers_group_by_offer` (line 508): `assert float(our_offer["spend"]) > 0`.
- `test_ads_last_alert_at`/`test_ads_last_disable_at`: timestamps, не money.

Round 10 `test_metric_aggregation_semantics.py` покрыл `/history/ads` и `/history/summary` мультицикловыми данными точно — но `/history/campaigns` и `/history/offers` **не покрыты семантически нигде** (только эти `>=`/`> 0` ассерты).
**Не проверяется:** точная сумма spend/leads/deposits == сумма latest-per-ad-per-day; campaigns/offers spend.

#### A-4. [CRIT] `test_api_dashboard_performance.py` — single-cycle, money-ассертов нет

- `test_performance_default_7d` (line 116): сеет 1 метрику → ассертит только наличие 3 ключей (`"top_campaigns" in data`). Чистый shape.
- `test_performance_top_campaigns_sorted` (line 176): 3 ad'а по 1 циклу → проверяет **сортировку** (`spend_vals == sorted(...)`), не точную сумму.
- `test_performance_offer_alerts_count` (line 199): `alerts_count >= 1` (lower-bound, под `if our_offer is not None` — может вообще не выполниться).
- `test_performance_rule_violations_unnest` (line 218): `count >= 2` под `if "CPC" in ...`.

`cost_per_lead` (вычисляемое из `SUM/NULLIF`) — **не проверяется численно ни в одном тесте этого файла**. Round 10 покрыл top_campaigns/leaderboard в отдельном файле, но `cost_per_lead` и `top_rule_violations` точное значение — нет.
**Не проверяется:** spend кампании/оффера (точно), `cost_per_lead` значение.

#### A-5. [HIGH] `test_api_dashboard_stats.py` — counts на `>=`

- `test_stats_counts_ads_by_state` (line 127): 5 ad'ов → `ads_in_warning >= 2`, `ads_in_stop >= 1`, `active_incidents >= 3`.
- `test_stats_*_tasks` (line 230, 262): `pending_disable_tasks >= 3`, `failed_tasks_24h >= 1`.
- `test_stats_scans_today` (line 299): `scans_today >= 2`, `scans_today_with_errors >= 1`.

`>=` на счётчике **не ловит двойной счёт** — типовой баг для composite-запроса с JOIN fan-out (например LEFT JOIN на ad_metrics удваивает count ad'ов). Хорошо: `test_stats_empty_db` (line 118) использует exact `== 0`.
**Не проверяется:** точное число (== N), отсутствие fan-out-дублирования.

> Корень `>=` по всему API-слою: `pg_engine` — **function-scoped, но БЕЗ rollback** (общая персистентная Postgres-БД, `conftest.py:58-72`). Изоляция — только через `clean_*` fixture с `DELETE ... WHERE code LIKE 'PREFIX_%'`. Глобальные агрегаты (summary/stats/chart-data) видят остаточные строки чужих тестов → авторы вынужденно ставят `>=`. **Правильный обход (применён в Round 10):** сверять точно ТОЛЬКО свою сущность (per-ad через `/history/ads` с уникальным `ad_name`-фильтром), а для глобальных — scoped-SQL по своим `ad_id`.

---

### B. Отсутствие контрактных тестов (writer↔reader)

#### B-1. [HIGH] Heartbeat: writer `WORKER_NAME` ≠ reader `EXPECTED_WORKERS` — нет контрактного теста

Это **прямой клон класса observer:runtime-бага**, ещё не закрытый.

- **Reader** (`apps/health_watchdog/main.py:39`): `DEFAULT_EXPECTED_WORKERS = "observer,disable,enable,telegram_poller,cleanup,reconciler,meta_api"`, читает `worker:heartbeat:{name}`.
- **Writers** пишут под именами: `digest_scheduler`, `creator`, `creator_recorder`, `enable_reco`, `meta_api`, `health_watchdog` (`grep WORKER_NAME apps/*/main.py`).

Расхождения: writer пишет `worker:heartbeat:enable_reco`, но в `EXPECTED_WORKERS` его НЕТ (есть `enable` — а `enable_worker` использует toggle_executor и, возможно, пишет другой ключ). `digest_scheduler`/`creator`/`creator_recorder` writer'ы дышат, но watchdog их по умолчанию **не мониторит**. И наоборот — если воркер переименует `WORKER_NAME`, watchdog молча перестанет его видеть (ровно `worker_status` vs `status`-сценарий). **Нет ни одного теста**, который сводит список реальных `WORKER_NAME` всех воркеров с `EXPECTED_WORKERS` reader'а.
**Severity HIGH:** молчаливая потеря мониторинга воркера = пропущенный инцидент.

#### B-2. [MID] Pubsub publisher payload-формат — частично закрыт

- **restart-каналы** (`fb_agent:worker:restart:*`): publisher шлёт `json.dumps({"requested_by","ts"})`, subscriber (`_on_restart(_payload)`) payload **игнорирует** — контракт сводится к имени канала, и `test_api_observer.py` его проверяет. **OK.**
- **`cabinet_day`/`trigger`**: publisher шлёт JSON, `RedisPubSubListener` делает `json.loads` (`core/pubsub.py:104`). Контракт «publisher эмитит валидный JSON» косвенно покрыт через `test_worker_subscribers.py` (там `json.dumps` в publish). `test_start_new_cabinet_day` подписывается на канал, но **не ассертит ни получение сообщения, ни его shape** (только DB-row + HTTP-resp) — pubsub-плечо в этом тесте холостое.
**Не проверяется:** что endpoint реально доставил сообщение в cabinet_day-канал (тест подписан, но не читает).

#### B-3. [хорошо] gRPC adapter, task_queue payload, enum — закрыты правильно

- `test_meta_api_adapters.py` — `MetaApiAdRow → ScannedAdRow` field-by-field + computed (образец).
- `test_toggle_worker_db.py` — `create_task(payload={fb_ad_id})` → `execute_one_toggle_task` читает payload → `gate.toggle_ad(fb_ad_id, target_state)` с проверкой точных аргументов + финального DB-статуса. Полный writer→reader контракт payload'а disable/enable.
- `test_status_mapper.py` — round-trip lowercase↔UPPERCASE + ValueError на мусоре.
- `meta_api_mutation` payload: `test_meta_api_outbox_e2e.py` сеет draft → approve → claim → dispatch (mocked) — outbox-контракт реален, dispatch замокан осознанно (handlers тестируются отдельно в unit).

---

### C. Mock'и, обессмысливающие тест

#### C-1. [MID] `test_worker_subscribers.py` — тавтология wiring

`test_observer_trigger_sets_force_scan_flag` (line 32) импортирует `CHANNEL_TRIGGER` и `_ObserverState`, но **определяет handler `_on_trigger` ЗАНОВО внутри теста** (line 44-47) и сам же регистрирует его в `RedisPubSubListener`. Тест доказывает, что `RedisPubSubListener` работает — но НЕ что `run_observer_worker` связал `CHANNEL_TRIGGER` именно с тем handler'ом, что ставит `force_scan_pending`. Если в реальном воркере перепутать `_on_trigger`↔`_on_restart`, тест останется зелёным. (То же для `test_observer_restart_sets_should_stop`, `test_disable_worker_restart_stops_loop`.)
**Не проверяется:** фактическая привязка канал→handler внутри воркера.

#### C-2. [LOW, by design] meta_api dispatch замокан в worker-тестах

`test_meta_api_outbox_e2e.py:113`, `test_e2e_ai_draft_to_mutation.py:150` — `monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)`. Это **корректно**: тестируется маршрутизация ошибок воркера (`RateLimited→requeue`, `TokenInvalid→failed`), а сами mutations — в `test_meta_api_mutations.py` через `call_args`. Не тавтология. (Round 8 §3.3 верно отметил, что generic-`Exception → requeue` ветка не покрыта — это gap покрытия, не фейк.)

#### C-3. [хорошо] AI meta-tools и mutations — mock на сетевой границе, ассерт на логике

`test_ai_tools_meta.py` мокает `InsightsFetcher`/`MetaApiClient` (сеть), но ассертит **форматирующую логику tool'а** (`"rows=2"`, `"id=100"`) — реальная логика выполняется. `test_meta_api_mutations.py` мокает `execute_graph_call` и инспектит `call_args.kwargs["query_params"] == {"status": "PAUSED"}` + `assert_not_awaited()` при валидации. Образцовый mock-discipline.

---

### D. Слабые ассерты

| Файл::тест | Слабый ассерт | Что нужно |
|---|---|---|
| `test_api_history.py::test_summary_happy_path` | `spend >= 300.0` (single-cycle) | `== <latest-sum>` на ≥2 циклах |
| `test_api_history.py::test_summary_default_30_days` | `spend > 0` | exact value |
| `test_api_dashboard_performance.py::test_performance_default_7d` | `"top_campaigns" in data` | значение spend/cpl |
| `test_api_dashboard_stats.py` (×6) | `count >= N` | `count == N` |
| `test_api_dashboard_chart_data.py` (все) | `len(buckets) >= 1` | `bucket["spend"] == latest` |
| `test_api_dashboard_spend_history.py` (все) | только `fb_ad_id` membership | `point["spend"] ==` |
| `test_ai_tools_meta.py` | `"rows=2" in out` (substring) | приемлемо (формат = логика) |
| `test_performance_offer_alerts_count` | `if our_offer is not None: assert >= 1` | guard прячет «оффер не найден» |

**Цифры по suite:** 182× `assert status_code == 200`, 140× `assert ... is not None`, 154× `assert len(...)`. Большинство сопровождается доп-ассертом, но money/count-зона — нет. `pytest.fail`/`except`-проглатывание — 8 случаев, из них `test_timeline_only_terminal_tasks` (line 333-365) использует `pytest.fail` внутри цикла (корректно) — реальных проглатывающих try/except не найдено.

**Тесты без содержательного ассерта (smoke):**
- `test_api_health.py::test_healthz_returns_200` — только status_code (приемлемо для liveness).
- `test_performance_days_30` (line 134) — `assert resp.status_code == 200` и всё, без сидинга и без проверки тела.

---

### E. Партиционные тесты-обманки

| Файл::тест | Вердикт |
|---|---|
| `test_api_dashboard_chart_data.py::test_chart_partition_pruning` (line 196) | **ОБМАН.** Сеет метрику 100h назад, ассертит лишь `isinstance(buckets, list)`. Не проверяет, что метрика ВНЕ окна исключена (не смотрит spend/active_ads). Прошёл бы и при утечке старой партиции. |
| `test_api_history.py::test_partitioned_query_timing` (line 568) | **ОБМАН (timing).** `elapsed < 2.0` ничего не доказывает о корректности: full-scan на почти пустой тест-БД тоже быстрый. Не сверяет данные. |
| `test_api_dashboard_spend_history.py::test_spend_partition_pruning` (line 154) | **ПРАВИЛЬНО.** Сеет in-window + out-of-window, ассертит `fid_old not in fb_ids`. Образец. |
| `test_dashboard_snapshot_rule_codes.py::test_old_alert_event_outside_lookback_is_excluded` (line 204, Round 10) | **ПРАВИЛЬНО.** Event 8 дней назад → `stop_rule_codes == []`. Образец. |
| `test_digest_builder.py` (×3, `t_old = now - 30h`) | **ПРАВИЛЬНО.** out-of-window snapshot/alert/task реально исключён из агрегата. |
| `test_get_recent_alerts_v2_schema.py` (`t_old = 100h`) | **ПРАВИЛЬНО.** |

Вывод: партиционная проверка «есть, но половина — обманки». Образцы существуют рядом — нужно перенести паттерн в chart-data и history-timing.

---

### F. Integration/unit перепутаны

- `tests/unit/test_dashboard_snapshot.py` — тестирует pure `_build_sql`/`_build_row_dict` (сборка SQL-строки и dict), без реального PG. **Корректно unit.** Бонус: `test_build_sql_contains_last_ev_lateral` ассертит наличие partition-фильтра в SQL-строке — умный statiс-guard.
- `tests/unit/test_mcp_context.py` — pure, корректно.
- `tests/integration/test_api_postback_security.py` — **named integration, но стабит `ingest_postback`** ("sync TestClient не работает с реальным БД-engine", line 46). Тестирует security-слой (compare_digest, body-size), реальный ingest — в `test_adset_pro_ingest.py`. Mild-mislabel, защитимо (фокус — auth).
- `test_cleanup_worker_db.py`, `test_reconciler_worker_db.py` — используют свой `create_async_engine` (не fixture `pg_engine`), реально бьют в БД. **Корректно integration.**
- `fake_redis_client` — настоящий `fakeredis.aioredis` (полноценная in-memory реализация). Redis-«интеграция» реально интегрирует клиентскую логику. **Не проблема.**
- В `tests/unit/` нет ни одного теста с `pg_engine`/`asyncpg`/docker → unit-папка чиста от БД.

---

## 3. Приоритизированный список усилений

CRIT = тест прикрывает money/data-correctness/security и сейчас фейк-зелёный.

| # | Sev | Файл::тест | Усиление |
|---|---|---|---|
| 1 | **CRIT** | `test_api_dashboard_chart_data.py::test_chart_bucket_day` | Уже сеет 3 цикла в день — добавить `assert Decimal(bucket["spend"]) == <latest>` (не SUM). Прямо ловит CRIT-1 эпицентр. |
| 2 | **CRIT** | `test_api_dashboard_chart_data.py::test_chart_bucket_hour` | Сеять 2 кумулятивных цикла в один час, ассертить spend бакета == latest. |
| 3 | **CRIT** | `test_api_dashboard_spend_history.py` (новый тест) | Сеять точку с известным spend, ассертить `point["spend"] == <value>` (контракт «точка несёт верный spend»). |
| 4 | **CRIT** | `test_api_history.py::test_summary_happy_path` | Заменить `>=` на мультицикловую сборку + exact `== latest-per-ad-per-day`; добавить per-campaign и per-offer exact (scoped по своей сущности). |
| 5 | **CRIT** | `test_api_dashboard_performance.py` (новый) | `cost_per_lead` exact (`spend/leads`) на мультицикловых данных — сейчас вычисляемое поле не проверено численно нигде. |
| 6 | **HIGH** | `test_api_dashboard_stats.py::test_stats_counts_ads_by_state` | `>=` → `==` (с надёжной изоляцией: seed уникальных ad'ов + filter, или scoped count). Ловит fan-out double-count. |
| 7 | **HIGH** | новый `tests/unit/test_heartbeat_contract.py` | Контракт: собрать `WORKER_NAME` всех воркеров (импортом) ∪ сверить с `EXPECTED_WORKERS` watchdog'а. Зафиксировать какие воркеры мониторятся, какие нет (B-1). |
| 8 | **HIGH** | `test_api_dashboard_chart_data.py::test_chart_partition_pruning` | Добавить in-window метрику + ассерт что spend бакета == in-window-only (out-of-window 100h исключён по значению, не по `isinstance`). |
| 9 | **HIGH** | `test_api_history.py::test_partitioned_query_timing` | Заменить timing-only на data-correctness: сеять row внутри и вне окна, ассертить что totals == только in-window. |
| 10 | **HIGH** | `test_api_dashboard_performance.py::test_performance_rule_violations_unnest` | Убрать `if "CPC" in ...`-guard, сделать exact `count == 2` (иначе guard прячет провал unnest). |
| 11 | **MID** | `test_worker_subscribers.py` (все 3) | Тестировать через фактическую функцию wiring'а воркера (или экспортировать `_build_listener(state)`), а не переопределённый локально handler — иначе перепутанная привязка зелёная (C-1). |
| 12 | **MID** | `test_api_observer.py::test_start_new_cabinet_day` | Дочитать pubsub-сообщение и ассертить его shape (`event == "new_cabinet_day"`) — сейчас pubsub-плечо холостое (B-2). |
| 13 | **MID** | `test_api_history.py::test_campaigns_group_and_sort` | Exact spend кампании (scoped), не только сортировка. |
| 14 | **MID** | `test_api_history.py::test_offers_group_by_offer` | `> 0` → exact spend оффера (scoped, мультицикл). |
| 15 | **MID** | `test_api_dashboard_stats.py` (tasks/scans counts) | `>=` → `==` для pending/failed/scans (seed + scoped). |
| 16 | **MID** | `test_performance_offer_alerts_count` | Убрать `if our_offer is not None`-guard → жёстко `assert our_offer is not None` + exact count. |
| 17 | **LOW** | `test_api_postback_security.py` | Документировать в docstring, что ingest застаблен и реальный ingest — в `test_adset_pro_ingest.py` (снять mislabel-двусмысленность). |
| 18 | **LOW** | `test_performance_days_30` | Добавить сидинг + хотя бы 1 содержательный ассерт (сейчас только `== 200`). |

**Сводно:** ~5 CRIT + 5 HIGH + ~6 MID + 2 LOW ≈ **18 усилений**, ~0.5–1 раунд. Все — переписать ассерт/фикстуру существующего теста (не новые сценарии), кроме #3, #5, #7, #12 (новые тесты/контракт).

---

## 4. Что хорошо (тесты-образцы)

1. **`test_metric_aggregation_semantics.py` (Round 10, 7 тестов)** — эталон семантики money: мультицикловые кумулятивы (10→50), exact `== 75` (не 375), многодневный reset (`50+30 == 80, не 165`), per-ad точно через `/history/ads` + scoped-SQL для глобальных. Это шаблон для всех усилений §3.
2. **`test_observer_runtime_contract.py` (Round 10, 13 тестов)** — эталон контракта: реальный `_publish_runtime_status` пишет → реальный `read_observer_runtime` читает → `scanning→running` нормализация проверена; writer пишет ОБА ключа (`worker_status`+`status`); E2E через `/observer/status` и `/dashboard/stats`. Шаблон для heartbeat-контракта (#7).
3. **`test_dashboard_snapshot_rule_codes.py` (Round 10, 6 тестов)** — образец partition-pruning: out-of-window event реально исключён + LATERAL `latest_wins` + HTTP-плечо.
4. **`test_digest_builder.py`** — почему `digest_builder` единственный без CRIT-1: `test_build_digest_top_ads_and_total_spend` сеет 2 цикла (60→100) + out-of-window 999 → `total == 150` exact. Правильный тест защитил правильный код.
5. **`test_meta_api_mutations.py` (23)** — инспекция `call_args.kwargs` (точный `method`/`endpoint`/`query_params`) + `assert_not_awaited()` при валидации. Mutation-контракт без сети, но с проверкой того, ЧТО уйдёт в Graph.
6. **`test_meta_api_adapters.py` (7)** — field-by-field + вычисляемые (`cost_per_lead == 20/4 == 5`, `ACTIVE → Active`). Образец adapter-контракта.
7. **`test_toggle_worker_db.py` (6)** — `_RecordingGate` ловит точные `fb_ad_id`/`target_state` из payload + DB-статус. Сквозной payload→executor→gate→DB контракт disable/enable.
8. **`test_rules_evaluator_property.py` (Hypothesis)** — 4 инварианта на random-метриках; в Round 8 нашёл реальный баг (`regs_no_dep_stop` без spend). Единственный property-based, но качественный.
9. **`test_status_mapper.py` (15)** — round-trip enum + ValueError-ветки.
10. **ACL/race-кластер** (`test_approve_draft_owner_acl.py`, `test_outbox_race_no_double_execution.py`, `test_alert_dispatcher_no_duplicate_send.py`) — реальные гонки через `asyncio.gather`, не моканы.

---

## 5. Метрика-оценка: семантические vs shape-only

Грубая выборочная оценка (по прочитанным ~25 файлам + экстраполяция на зоны).

| Зона | Тестов (≈) | Семантических | Shape-only/слабых | Комментарий |
|---|---|---|---|---|
| **Money-агрегации** (history, performance, chart, spend, offers/compare) | ~45 | ~40% | **~60%** | Худшая зона. Round 10 добавил 7+1 правильных, но старые `>=`/single-cycle остались. |
| **Counts/stats** (dashboard_stats, incidents) | ~25 | ~55% | ~45% | incidents точны (`==`), stats на `>=`. |
| **Контракты** (adapters, mapper, toggle, runtime) | ~50 | **~90%** | ~10% | Сильнейшая зона. heartbeat — единственный gap. |
| **Mutations/meta_api** (mutations, batch, upload, dispatch) | ~120 | **~90%** | ~10% | `call_args`-инспекция повсеместно. |
| **FSM/observer/outbox/race** | ~90 | **~85%** | ~15% | property-based + concurrent. |
| **Telegram/AI-tools** | ~110 | ~75% | ~25% | mock-discipline хорош, substring-ассерты местами. |
| **Партиционные проверки** | ~15 | ~60% | ~40% | 2 обманки (chart, history-timing), остальные правильны. |

**Итого по suite (взвешенно):** ориентировочно **~72–78% семантических, ~22–28% shape-only/слабых**. Слабая четверть **сконцентрирована** в money-агрегациях и counts — то есть ровно там, где цена бага высшая, и ровно того же класса, что родил оба CRIT. Контрактное/mutation/FSM-ядро — здоровое (~85–90% семантики).

**Вывод метрики:** suite НЕ системно фейковый. Но **концентрация слабости совпадает с зоной максимальной цены ошибки** — поэтому 1028 зелёных тестов и пропустили money-CRIT. Один прицельный раунд (§3) сдвигает money-зону с ~40% до ~85% семантики и устраняет класс.

---

## Приложение: как именно прошли оба CRIT (хронология слепоты)

```
CRIT-1 (SUM cumulative_spend):
  test_api_history.py        — 1 snapshot/ad + assert spend >= 300   → SUM==latest на 1 цикле, >= прячет ×N
  test_api_dashboard_perf    — 1 snapshot/ad + assert "key" in data  → money не ассертится вовсе
  test_api_dashboard_chart   — 3 цикла/день, но assert len>=1        → данные ЕСТЬ, ассерт не на spend
  test_digest_builder        — 2 цикла + assert total==150 (latest)  → ПРАВИЛЬНО → digest без бага
  ⇒ единственный модуль с мультицикловым exact-ассертом — единственный корректный агрегатор.

CRIT-2 (worker_status vs status):
  observer writer test       — свой mock, проверяет что писатель пишет (свой ключ)
  dashboard_stats reader test— assert observer_status == "unknown"   → ЗАФИКСИРОВАЛ баг как ожидание
  observer reader test       — свой mock, проверяет чтение (свой ключ)
  ⇒ нет теста где РЕАЛЬНЫЙ writer → РЕАЛЬНЫЙ reader; обе стороны «зелёные» по отдельности.
```

Оба — следствие одного антипаттерна: **проверять стороны изолированно на данных/моках, не нагружающих границу, где живёт баг.** Round 10 закрыл два конкретных случая правильными файлами; оставшиеся однотипные shape-only тесты (§3 #1–#10) — тот же класс, ждут усиления.
