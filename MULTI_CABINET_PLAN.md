# MULTI_CABINET_PLAN.md — поддержка нескольких рекламных кабинетов

> Master source of truth для работы «мульти-кабинет».
> Статус 2026-06-09: **M1–M5 реализованы** (код + тесты). Перед запуском обязательны:
> 1. `make proto-compile` — перегенерить Python-стабы (новые поля `ad_account_id` в
>    `RunScanCycleRequest` / `ExecuteGraphCallRequest`; TS грузит proto динамически, ему не нужно);
> 2. `alembic upgrade head` — миграция `0019_multi_cabinet`;
> 3. `make verify` + `cd services/browser-agent && npm test` (138 TS-тестов прошли в ходе работ);
> 4. `pnpm gen:api` (типы `ad_account_ids` во фронте) + `pnpm --filter fb-stop-bot-frontend test`;
> 5. Заполнить кабинеты у активных офферов (UI Offers → «Рекламные кабинеты») — без этого
>    observer работает в legacy-режиме одной вкладки.
> Связанные документы: `META_INTEGRATION_PLAN.md` (канал Marketing API), `DB_REDESIGN.md` (схема).

## 0. Согласованные решения (2026-06-09)

| Вопрос | Решение |
|---|---|
| Где хранить ID кабинетов | **Per-offer**: у каждого оффера список `ad_account_ids` (минимум 1). Scan set = объединение списков всех активных офферов. |
| Порядок сканирования | **Последовательно**: один цикл observer = каб1 → каб2 → ... Скан кабинета через am_tabular — секунды, поэтому реакция на стоп-правило растёт лишь на +5–15 с к interval (см. §8). |
| Управление вкладками | **Бот сам**: ищет вкладку с нужным `act=` среди открытых, при отсутствии открывает `adsmanager.facebook.com/adsmanager/manage/ads?act=<id>`. Пользователь может открыть вкладки заранее, но не обязан. |
| owner_campaign_tag | **Общий** на все кабинеты (текущая логика `campaign_matches_owner` не меняется). |

## 1. Текущее состояние (где зашит один кабинет)

- `services/browser-agent/src/am/am-fetch.ts` — `extractGraphContext()` вытаскивает `act_<id>` + EAAB-токен из запросов открытой вкладки; кэш `_graphContextCache` ключуется **только по session_id** → при двух вкладках перезатирается.
- `services/browser-agent/src/index.ts` — `getPage(session, pageId)` бросает ошибку при `pageId != null`: одна primary page на сессию.
- `core/models/settings/observer_config.py` — singleton, поля `account_id` нет.
- `fb_campaigns` / `scan_runs` — нет колонки `ad_account_id`.
- `core/meta_api/mutations/*` — мутации по `ad_id`, кабинет в endpoint не нужен; но fetch исполняется внутри страницы → желателен роутинг во вкладку «своего» кабинета.
- Токен EAAB общий для всех кабинетов юзера — технически fetch на `act_X` работает из вкладки `act_Y`, но держим вкладку-на-кабинет ради «человеческого» паттерна (антидетект).

## 2. Этап M1 — схема БД и каталог

1. **`offers.ad_account_ids`** — `JSONB` (list[str]), NOT NULL после backfill. Валидация в API: минимум 1 элемент, формат `^\d+$` (без префикса `act_`).
   - Миграция Alembic: колонка nullable → backfill-скрипт проставляет текущий кабинет (вводится оператором или берётся из `fb_campaigns` после M3) → отдельной миграцией NOT NULL.
2. **`fb_campaigns.ad_account_id TEXT`** + индекс. Заполняется в `core/observer/writers.py::upsert_catalog_hierarchy` из контекста скана. Связь ad → account дальше резолвится через JOIN (в `fb_ads` колонку не дублируем).
3. **`scan_runs.ad_account_id TEXT NULL`** — какой кабинет сканировался (partitioned-таблица, добавление колонки безопасно).
4. **`core/observer/accounts.py`** (новый) — `resolve_scan_account_ids(engine) -> list[str]`: DISTINCT union `ad_account_ids` активных офферов, стабильный порядок (сортировка). Пустой список → fallback на старое поведение (скан текущей вкладки) + warning-лог.

## 3. Этап M2 — browser-agent (TypeScript + proto)

1. **Proto**: `RunScanCycleRequest.ad_account_id` (string, опционально — пустое значение = старое поведение). Аналогично `ExecuteGraphCallRequest.ad_account_id`.
2. **SessionManager**: карта `actId → page`.
   - `ensureAdsManagerPage(actId)`: среди открытых вкладок ищем URL с `act=<id>`; нет — открываем новую вкладку Ads Manager этого кабинета; перед сканом `bringToFront()`.
   - Снять запрет в `getPage()` (ошибка «Поддержка нескольких страниц пока не реализована»).
   - Self-heal (Layer 1) — per-кабинет: закрытая вкладка переоткрывается по `reconstructAdsManagerUrl(actId)`.
3. **am-fetch.ts**:
   - Кэш GraphContext по ключу `${sessionId}:${actId}` (фикс перезатирания).
   - `extractGraphContext(page)` слушает запросы конкретной вкладки; sanity-check: `actId` из перехваченного запроса == запрошенный, иначе ошибка «вкладка открыта не на том кабинете».
4. **ExecuteGraphCall**: если передан `ad_account_id` — fetch исполняется во вкладке этого кабинета; не передан — текущая primary page (backward-compat).
5. **Page-lock**: `withPageLock` остаётся per-session (НЕ per-page) — сериализует сканы и мутации всех вкладок. Цена — мутация ждёт секунды до конца текущего скана; выигрыш — нет гонок «reload для сниффа во вкладке A во время evaluate во вкладке B».
6. **Холодный снифф токена** — один раз на кабинет: первый скан новой вкладки делает `page.reload` + снифф (до ~20 с). В стационаре reload'ов нет — только fetch'и (~4–6 запросов, секунды на кабинет).

## 4. Этап M3 — observer_worker

1. `run_one_cycle`: загрузка `resolve_scan_account_ids()` → последовательный проход. На каждый кабинет: свой `_begin_scan_run(ad_account_id)` → `gate.run_one_scan(ad_account_id=...)` → `process_scan_rows(..., ad_account_id=...)` (каталог пишется с кабинетом). Пауза 2–5 с между кабинетами.
2. Ошибка скана одного кабинета НЕ прерывает остальные: лог + `scan_runs.outcome='error'` per кабинет, цикл продолжается.
3. `observer:runtime` (Redis): добавить `current_account_id`, `accounts_total`, `accounts_done` — фронт/`/observer/status` показывают прогресс.
4. `interval_seconds` — пауза между ПОЛНЫМИ циклами (всеми кабинетами), не между кабинетами. Scan-now (`fb_agent:observer:trigger`) запускает полный цикл.
5. **Адаптивный интервал** (`core/observer/adaptive_interval.py`): режим цикла = worst-case по всем кабинетам — stop-хит в любом кабинете → CRITICAL (×0.2) для всего цикла, warning → ELEVATED. Агрегация `select_scan_mode` по суммам alerts_stop/alerts_warning/rows_with_offer всех кабинетов.

## 5. Этап M4 — mutations и bulk

1. `MetaMutationPayload.ad_account_id` заполнять всегда:
   - auto-stop в observer — кабинет известен из контекста скана;
   - ручные задачи (TG inline `dis:`, `/ads`, AI drafts) — резолв через `fb_ads → fb_adsets → fb_campaigns.ad_account_id`.
2. `meta_api_worker` прокидывает `ad_account_id` в `ExecuteGraphCall` → мутация уходит из вкладки своего кабинета.
3. Bulk-пути (`bulk_status_change`, autostart, AI drafts) НЕ требуют кабинета для корректности: batch идёт точно по ad_id, EAAB-токен общий → исполняются с primary-вкладки (`ad_account_id=None`). Per-cabinet фильтр `resolve_owner_ad_ids_by_dates` и split autostart-задач по кабинетам — отложено (вернуться, если появится потребность в per-cabinet расписаниях).
4. `insights/fetcher.py::fetch_for_ads` уже принимает `ad_account_id` — источник теперь каталог, не константа.

## 6. Этап M5 — API и фронтенд

1. `offers.py` router: `ad_account_ids` в `OfferOut` / `OfferCreateIn` / `OfferUpdateIn`, валидация min 1.
2. `frontend/` `OfferFormModal`: поле «Кабинеты» (мульти-ввод ID, минимум 1). `pnpm gen:api` после обновления openapi.
3. Settings → Observer: read-only отображение итогового scan set (union) — чтобы было видно, что реально сканируется.
4. Dashboard/Ads: колонка/фильтр по кабинету — отдельным шагом, не блокирует M1–M4.

## 7. Тесты (комментарии на русском над каждым)

- Unit: `resolve_scan_account_ids` (union, дедуп, пустой список, сортировка); ключ кэша GraphContext; sanity-check act mismatch.
- Integration: два кабинета в одной БД — метрики/каталог не смешиваются (`fb_campaigns.ad_account_id` корректен per scan); mutation для ad из каб2 получает `ad_account_id` каб2; ошибка скана каб1 не ломает скан каб2.
- Contract: proto-поля `ad_account_id` writer↔reader (по образцу `test_heartbeat_contract.py`).
- Регресс: один кабинет / пустые `ad_account_ids` → поведение идентично текущему.

## 8. Риски и заметки

- **Реакция на STOP почти не растёт**: скан кабинета через `am_tabular` — пара Graph-fetch'ей (`limit=5000`, обычно одна страница) = секунды. Худший случай реакции = `interval_seconds` + сумма времени сканов всех кабинетов (на 2–3 кабах +5–15 с к 90 с). Следить за rate-limit Meta при росте числа кабинетов — единственный фактор, способный растянуть цикл.
- **Дубли кампаний между кабинетами**: offer-матчинг по названию остаётся, но `resolve_owner_ad_ids_by_dates` без фильтра по кабинету мог бы зацепить чужой каб — закрывается фильтром в M4.3.
- **Digest/аналитика** пока без разреза по кабинету (суммарно) — отдельный backlog-пункт.
- **Backfill** `offers.ad_account_ids`: до заполнения оффер без кабинетов исключается из scan set с warning в TG ops-топик.
- Порядок внедрения: M1 → M2 → M3 (после этого мульти-скан работает) → M4 (мутации) → M5 (UI). M2 и M1 можно параллелить.
