# Аудит бота и UI — FB Stop Bot

**Дата:** 2026-06-28
**Scope:** весь бот (backend: `core/`, `apps/`, `services/browser-agent/`) + UI (`frontend/` web, `frontend-mini/` TMA)
**Метод:** 8 ревьюеров по доменам (workflow) → адверсариальная верификация каждой CRIT/HIGH независимым скептиком (открывал реальный код, переоценивал severity, отсекал ложные).
**Агентов:** 12 (8 ревью + верификаторы), ~2.16M токенов. Домен `api-routers` завис в основном прогоне → добит отдельным сфокусированным агентом.

## Вердикт

**CRIT — нет. Подтверждённых HIGH после верификации — 1** (money-leak в стоп-правиле registration-stage). Костяк (partition-pruning, naive-SUM денег, timing-safe секреты, CORS-guard, outbox-идемпотентность, FSM-гарды, batch-encode) — **чистый**: все исторические классы багов проекта в API-слое не воспроизвелись. Остальное — MID/LOW: тех-долг, краевые случаи, пробелы в тестах, UI-косметика.

Верификация отработала как фильтр: из 4 заявленных HIGH **1 подтверждён HIGH, 1 понижен до LOW, 1 понижен до MID, 1 признан false-positive**.

## Таблица severity × домен (после верификации)

| Домен | HIGH | MID | LOW |
|---|---|---|---|
| observer / rules / scanner | **1** | 2 | 1 |
| meta_api / tasks / mutations | — | 1 | 2 |
| workers | — | 2 | 2 |
| api routers | — | 1 | 2 |
| models / migrations / dashboard / adset_pro | — | 1 | 2 |
| frontend web | — | 5 | 4 |
| frontend mini | — | 3 | 2 |
| tests / cross-cutting | — | 1 | 1 |
| **Итого** | **1** | **16** | **16** |

---

## HIGH (подтверждён)

### H1 — Стоп-правило registration-stage не страхует спенд при `cost_per_registration = None`
`core/rules/evaluator.py:149-183` (`_evaluate_registration_stage` + `_is_registration_normal:506-518`)

**Проблема.** При `registrations ≥ 1` и `cost_per_registration = None` (Meta вернула count событий в `actions`, но ещё не посчитала `cost_per_action_type` — штатный attribution-лаг) registration-ступень остаётся без spend-guardrail'а: `cpr_hit = None` (метрика None), `regs_no_dep` требует 4-5 регистраций, а `spend_no_dep_range` гейтится через `_is_registration_normal()`, который при `CPR = None` возвращает `False` и правило не запускается вовсе. Click/lead-ступени имеют raw-spend backstop следующей ступени — у registration такого нет.

**Верифицировано (REAL, HIGH).** Дифференциальный прогон `evaluate_stop_rules`: CPA=$100, spend=$95 (95% CPA), 3 регистрации, 0 депозитов, `external_deposits=0` → `stage=None` (НЕ застопорено); тот же ад с `CPR=$15` → `STOP spend_no_dep_range`. Разница в выходе определяется **исключительно** тем, что CPR временно None. Реальность NULL подтверждена в TS-сканере (`am-join.ts:123`: `amNum(costPerAction[omni_complete_registration])` → null при отсутствии записи). Кодовая база уже распознаёт это состояние (`_has_enable_data_gap`, `evaluator.py:526`), но консультируется с ним только в enable-направлении, а в stop — нет.

**Impact (money).** Убыточный ад с регистрациями, без депозитов и с временно-NULL CPR крутит бюджет без авто-стопа, пока CPR не появится или регистрации не дорастут до 4-5. Окно самоограничено (лаг резолвится за минуты-часы, на следующем скане ад стопается; при ≥4-5 регах `regs_no_dep` ловит независимо от CPR) — потому HIGH, не CRIT. Но триггер достижим и бьёт прямой перерасход в полосе 1-4 регистрации.

**Fix.** В `_evaluate_registration_stage` запускать `spend_no_dep_range` также при `cost_per_registration is None` (CPR неизвестна = не подтверждена как нормальная). Тест: registrations 1-4 + CPR=None + spend ≥ stop_no_dep + external_deposits=0 → STOP.

---

## MID

### Деньги / надёжность
- **M1 — Пороги оффера writable-но-мёртвые.** `core/observer/queries.py:28-33` + `pipeline.py:66-119`. `OfferRules` грузит `spend_no_event_threshold` / `cpm_threshold` / `ctr_threshold` / `funnel_ratio_threshold`, но НИ ОДИН не пробрасывается в `RuleContext` — evaluator считает пороги как фикс-проценты от `cpa_threshold`. UI `RulesForm` их пишет, оператор думает что настраивает стоп, движок игнорирует → ложное чувство защиты. (`spend_no_event_threshold` как абсолютный backstop заодно закрыл бы H1.)
- **M2 — Партиционные таблицы без DEFAULT-партиции.** `migrations/versions/0001_*.py:83-99` + `apps/cleanup_worker`. `adsetpro_postback_events` (и класс `ad_metrics`) — RANGE без DEFAULT; партиции создаёт только cleanup_worker раз в сутки. Если воркер мёртв на стыке месяцев — INSERT postback'а падает `no partition found` → потерян депозит/FTD → `external_deposits` недосчитает → evaluator может ложно застопить прибыльный ад. Fix: DEFAULT-партиция на все partitioned-таблицы, либо ingest создаёт партицию on-the-fly при `UndefinedTable`.
- **M3 — Автостарт кабинета молча отбрасывает >50 объявлений.** `core/meta_api/bulk.py:70-122` + `apps/cabinet_scheduler/main.py:144-184`. Резолв с `limit=MAX_BULK=50` в один `bulk_status_change activate`; при >50 owner-ad включаются первые 50, остальные молча на паузе весь день (done-маркер + idempotency_key блокируют повтор). Ручной `/resume` про усечение предупреждает — автостарт нет. Fix: чанковать по 50 с уникальным `idempotency_key:{chunk}` либо алерт при `total > взятых`.
- **M4 — campaign_creator_worker валится при ошибке БД в фазе гардов.** `apps/campaign_creator_worker/main.py:432-452` + `90-168`. `task_loop` оборачивает try/except только `_claim`; `process_one_task` делает DB-I/O в pre-execute гардах ВНЕ try. Транзиентная ошибка БД → падает весь воркер. Задача застревает `running` → reconciler уводит в `failed` БЕЗ ретрая → подтверждённый залив теряется. `meta_api_worker`/`creator_worker` оборачивают тело целиком — здесь асимметрия. Fix: обернуть `process_one_task` единым try с маршрутизацией в requeue/mark_failed.
- **M5 — cabinet_scheduler force-сканит весь день при `no_owner_ads`.** `apps/cabinet_scheduler/main.py:194-204`. В окне без найденных owner-ад done-маркер не ставится, каждый тик (60с) заново `publish fb_agent:observer:trigger` → до ~960 форс-сканов/день. Обнуляет адаптивный интервал observer'а → сканы летят максимально часто → anti-detect риск. Fix: отдельный Redis-маркер `scan_triggered:YYYY-MM-DD` (SET NX) на observer-trigger.
- **M6 — `start_date` в прошлом принимается визардом.** `frontend/src/components/domain/campaigns/WizardStep3Goal.tsx:323`. Валидируется только наличие. Дата в прошлом уходит в Meta → невнятная ошибка на шаге залива (не на валидации) или нулевой спенд если кабинет-день уже сбросился. Fix: `if (start_date < today) errors.start_date = 'не может быть в прошлом'`.
- **M7 — frequency_outlier_cap глушит реальное выгорание.** `core/rules/evaluator.py:224-225` + `types.py:131-134`. Плоский потолок 10.0: при `freq > cap` правило молчит. Цель — отсечь шум на крошечном reach, но cap не смотрит на reach/impressions (они есть в контексте) → глушит и `freq=12` на большом охвате. Fix: гейтить выброс по reach, не плоским потолком.

### ACL / решение продукта
- **M8 — TMA disable/snooze/claim доступны любому recipient.** `apps/api/routers/v1/tma.py:351-482`. `is_owner` проверяется только в `PUT /cabinet-autostart`. Любой recipient с валидным токеном может отключить ад / заснузить алерт. **Оговорка:** это паритет с уже существующим поведением (recipient'ы и так жмут inline-кнопку `dis:` под алертом в TG) — то есть скорее by-design, чем дыра. Решение продукта: оставить паритет или owner-gate'ить money-действия и в TMA, и в inline-кнопках.

### UI / типобезопасность
- **M9 — Небезопасные cast'ы в web.** `frontend/src/lib/api/client.ts:76` (non-JSON → `text() as unknown as T`, при HTML-502 каллер молча получает строку); `routes/offers/$id.tsx:32` (`useParams() as unknown`); `routes/index.tsx:138,143-144` (`recent_alerts`/`disableTasks`/`enableTasks as ...` — OpenAPI типизирует их как `{[k]:unknown}[]`). Бэк сейчас отдаёт правильную форму, рантайм-бага нет, но drift не ловится компилятором. Fix: бросать типизированный `ApiError`; убрать cast'ы; уточнить OpenAPI `$ref` или zod-валидация на границе batch.
- **M10 — WS-инвалидация не покрывает `['campaigns','runs']`.** `frontend/src/lib/websocket/useRealtimeInvalidation.ts`. История заливов не обновляется live при смене статуса задачи (только по 15с-поллингу). Fix: добавить ключ в обработчик `task_changed`.
- **M11 — generated.ts отстал от бэка по creative-полям.** `packages/shared/src/api/generated.ts:4635`. `TmaAdDetailResponse` в сгенерированном типе НЕ содержит `creative_image_url`/`creative_thumb_url` (которые я добавил на бэк). Работает только потому, что mini читает из локального `TmaAdDetail` (`lib/api.ts`). `pnpm gen:api` из stale `openapi.json` снова их выкинет. Fix: перегенерить openapi.json + `pnpm gen:api`; в идеале — gen:api в CI как drift-чек.

### Пробелы в тестах
- **M12 — Гонка в UploadVideo не покрыта ни одним тестом.** `services/browser-agent/src/meta-api/service.ts:211-339`. Свежепочиненная гонка (очередь `pendingChunks` + одиночный воркер `processChunks`) без теста — ровно тот класс, что просочился сквозь 1000+ тестов. Регрессия порядка чанков → битое/0-байтное видео → ад с пустым крео тратит бюджет. Fix: node:test на `uploadVideoHandler` (порядок чанков, re-entry guard, end без метаданных, 0 байт).
- **M13 — StepPreview / StepLaunch визарда mini без тестов.** `frontend-mini/src/tests/campaigns.steps.test.tsx`. Последние два шага перед реальным созданием кампании. Нет регресс-защиты на guard «не пускать без validate.data» и `launched`-guard от двойного `POST /launch`. Fix: добавить describe-блоки на оба шага.

### Из этой же сессии (мои недавние правки)
- **M8/M11/M12** прямо касаются того, что я трогал: TMA-эндпоинты, creative-поля, фикс гонки UploadVideo.
- **mini handlePause без подтверждения** — см. ниже (после верификации MID), это моя новая кнопка стоп.

---

## Понижено верификацией (заявлено HIGH → по факту ниже)

- **Sparkline на кумулятивном спенде → LOW.** `frontend/src/routes/index.tsx:127-130,258`. Спарклайн строится из сырых кумулятивных снимков → весь день выглядит растущим независимо от реального часового траффика. **Подтверждено по коду, но severity LOW:** headline-спенд (`current_day_spend`) server-authoritative, KPI/авто-стоп/FSM нетронуты — это косметика одной ячейки. ⚠️ В предложенном автором фиксе **фактическая ошибка**: `chart-data?bucket=hour` НЕ возвращает SUM-per-bucket — бэк отдаёт кумулятив-per-bucket (`dashboard_timeseries.py:170-197`). Валиден только delta-вывод (`spend[i]-spend[i-1]`, clamp≥0 на сбросе суток).
- **mini handlePause без tgConfirm → MID.** `frontend-mini/src/routes/index.tsx:128-138` (моя новая кнопка стоп). Отключает observer (= авто-стоп всех адов) без подтверждения. **Подтверждено REAL, но MID, не HIGH:** состояние обратимо одним тапом Resume; вся шапка громко переключается на «ПАУЗА» (низкое время обнаружения); один намеренный тап по 44px-кнопке. Money-UI дефект, чинить как `handleDisable` рядом (`routes/ads/$fbAdId.tsx:55` — `tgConfirm`), но не HIGH. Fix одной строкой: `if (!(await tgConfirm('Остановить сканирование и авто-стоп?'))) return;`

## Отсеяно верификацией (false-positive)

- **WizardStep7Launch «бесконечный поллинг» — FALSE POSITIVE.** `frontend/.../WizardStep7Launch.tsx:194,212`. Заявлено: неизвестный статус с бэка → поллинг не останавливается → флуд API. **Недостижимо:** `campaign_run.status` имеет DB-level `CHECK IN (...)` (`core/models/campaigns/run.py:31-39`), писатели пишут только канон + маппер клампит неизвестную стадию в `creating`, frontend union == backend == DB CHECK один-в-один. `'queued'` — известный нетерминальный (поллинг корректен), `'timeout'` — невозможен. Хард-таймаут был бы nice-to-have, но живого бага нет.

---

## LOW (тех-долг, кратко)

- **God-components >500 строк (правило проекта):** `frontend/.../AdDrawer.tsx` (638), `WizardStep5Creatives.tsx` (636), `routes/ads/index.tsx` (531). Разнести на под-компоненты.
- **Дубль SQL:** `core/dashboard/snapshot.py:399-477` — `_build_sql_cursor` ≈ `_build_sql` (~80 строк, риск рассинхрона offset/cursor-пагинации при добавлении колонки).
- **`runtime.py:46`** — reader держит мёртвый статус `preparing`, которого writer не пишет (контракт writer↔reader без текущего эффекта).
- **`meta_api_worker` alert при провале bulk-activate** шлёт текст «отключи вручную» (направление перепутано) — только косметика TG-сообщения.
- **`custom_audience` action=create** не в `IRREVERSIBLE_MUTATION_KINDS` — при transient-retry возможен дубль аудитории (бюджет не тратит).
- **`tracker_aggregate.revenue`** = SUM по всем событиям дня, `deposits` = COUNT FILTER(deposit_types) — семантический рассинхрон (таблицу пока никто не читает).
- **aggregator stale-строки** не обнуляются если в окне не осталось валидных событий (`core/adset_pro/aggregator.py:116-140`).
- **cleanup_worker без catch-up** — пропуск суточного прогона если воркер был мёртв в 04:00 UTC (безвреден).
- **`campaigns_create.py:106`** — `daily_budget_cents` без Pydantic `ge=1` (domain-валидатор ловит, но позже/невнятнее).
- **`dashboard_stats.py:407`** — `enable_recommendations` без time-фильтра (LIMIT 5 защищает от большого ответа, но seq scan возможен).
- **`core/auth/tma.py:33-43`** — `data_check_string` не исключает поле `signature` (forward-compat риск Ed25519-flow, сейчас no-op).
- **mini StepLaunch `useState`-guard** vs React StrictMode (только dev; прод без StrictMode, бэк дедупит по idempotency_key).
- **mini `alertRuleCodes=[]`** захардкожен — danger-callout с кодами правил никогда не рендерится (`routes/ads/$fbAdId.tsx:144`); TMA не отдаёт `rule_codes` в истории алертов.

---

## Рекомендованный план

**Чинить первым (money/reliability):**
1. **H1** — registration-stage spend-backstop при CPR=None. Прямой money-leak, fix локальный + тест.
2. **M1** — подключить `spend_no_event_threshold` к evaluator (или убрать из формы). Закрывает «мёртвые пороги» и заодно усиливает H1.
3. **M2** — DEFAULT-партиции (потеря postback = ложный стоп прибыльного ада).
4. **M4** — обернуть `campaign_creator_worker.process_one_task` (потеря подтверждённого залива).
5. **M3 / M5** — автостарт >50 ад + force-scan спам.

**Быстрые однострочники:**
6. mini handlePause `tgConfirm` (моя кнопка); M6 `start_date` в прошлом; M11 `pnpm gen:api`.

**Тех-долг по мере касания:** god-components, дубль SQL, TS-cast'ы, M12/M13 тесты (UploadVideo-гонку — желательно раньше, money-путь без покрытия).

**Не трогать:** WizardStep7 (false-positive), sparkline (косметика, и предложенный фикс был неверен — если делать, то delta-вывод).
