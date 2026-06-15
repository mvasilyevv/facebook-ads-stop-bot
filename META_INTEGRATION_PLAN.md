# Marketing API Integration Plan — FB Stop Bot (v4, browser-agent gRPC strategy)

> **Дата:** 2026-05-25
> **Версия:** 4 (полностью переработана после реального эксперимента и deep-research архитектуры Dolphin/FBTool)
> **Статус:** master source of truth
> **Что заменяет:** все предыдущие версии плана + удалённые `META_MCP_INTEGRATION_BRIEF.md` и `META_MCP_RESEARCH.md`
> **Референсы:** deep-research extraction практик (ресёрч-выхлоп архивирован)

---

## Что изменилось от v3 (EAAbs standalone strategy)

В версии v3 я опирался на гипотезу «извлечь EAAbs из page source → использовать с Python httpx через Vision proxy». **Это архитектурно невозможно.** Подтверждено реальным экспериментом:

- Token `EAABsbCS...` извлечён из page source Ads Manager успешно
- При попытке `GET /me?access_token=$TOKEN` через прямой запрос — `error.code=1, type=OAuthException, message="Invalid request"`
- То же с `/debug_token` — отвергает на самом входе Graph API

**Причина:** EAAB-токены Ads Manager привязаны к browser session через `machine_id`, `datr` cookie, fingerprint, IP. Это session-bound токены, не portable. Когда запрос идёт из другого окружения (Python с VPS) — anti-fraud Meta отвергает на входе.

**Как реально работают Dolphin Cloud / FBTool** (подтверждено исходниками: [gist dvygolov createautorules.js](https://gist.github.com/dvygolov/c2077f391bd15ba2f75d7496afb47a67), [showbmadacclimit.js](https://gist.github.com/dvygolov/a480da9bfda8e1e01aeec65bf08487d7), [Wevion security research](https://wevion.ai/en/blog/token-cookie-facebook-ads-security/), [Youssef Sammouda](https://ysamm.com/uncategorized/2026/01/15/steal-dtsg-cookie.html)):

Они не делают standalone HTTP-запросы. Они делают **session-tunneled requests из самого браузера**: `page.evaluate(fetch(...))` или Chrome extension content script. Запросы идут к public Marketing API (`graph.facebook.com/v22.0/...`), но **из browser context** — с теми же cookies, fingerprint, IP, что и страница Ads Manager.

То есть архитектурно — это **тот же канал, что наш DOM-парсер**, только формат данных JSON вместо HTML.

---

## Решение в одном абзаце

В проекте остаётся **полный Vision-канал** для real-time операций (observer/disable/enable). Параллельно **расширяется `services/browser-agent/` новым gRPC-сервисом `MetaApiService`**, который исполняет запросы к Marketing API изнутри активной Playwright-сессии через `page.evaluate(fetch(...))`. Python-сторона `core/meta_api/` — **тонкий клиент** над gRPC, без httpx, без token storage, без прокси. Токен и cookies берутся на лету из живой browser-сессии в момент каждого вызова. AI-ассистент расширяется tools поверх этого gRPC. Дополнительно — `core/ad_library/` (App Access Token через свой Meta App — независимый канал) и `core/adset_pro/` (REST API трекера — независимый канал).

---

## 1. Принцип разделения каналов

| Операция | Канал | Реализация |
|---|---|---|
| Observer scan каждую минуту | Vision (DOM) | Существующий `ScannerService` |
| Disable / Enable объявления | Vision (DOM) | Существующие toggle-методы |
| Refresh таблицы Ads Manager | Vision (DOM) | Существующий метод |
| Эскалаторы хрупкости | Vision | Существующие |
| **Marketing API** (читать insights, mutations, create campaigns) | **Vision + MetaApiService gRPC** | Новый сервис в browser-agent, `page.evaluate(fetch)` к `graph.facebook.com` |
| Detection account disabled | Vision (через observer цикл) | Существующее |
| Анализ креативов конкурентов | **Ad Library API** | App Access Token, **отдельный канал**, через свой Meta App (без App Review) |
| Post-click данные (FTD, hold, redep, LTV) | **AdSet.pro REST API** | Независимый канал, без Vision |
| AI conversational analytics | **AI tools** | Вызывают MetaApiService gRPC + читают БД бота |
| AI conversational creator | **AI tools** + MetaApiService | Draft-first + gRPC create |

**Ad Library и AdSet.pro — независимы от Vision.** Это важно: эти каналы продолжают работать, даже когда Vision-сессия падает или фарм-юзер вылетел. Их данные используются для аналитики и AI-помощника даже в degraded режиме.

---

## 1.1. Live-замер latency Marketing API (BL-7, выполнен 2026-05-29)

**Метод.** Замер end-to-end на живой Vision-сессии: `Python execute_graph_call → gRPC → browser-agent → page.evaluate(fetch) → SOCKS-прокси профиля → graph.facebook.com → обратно`. Только GET-запросы (read-only, рекламу не трогали). N=8 измерений + 1 прогрев на тип, пауза 0.3с. Кабинет реального фарм-аккаунта (19 ad accounts, из них 8 активных, 3 с реальным трафиком). Account-id не приводятся (PII).

**Результаты (медиана / p90 / max, мс):**

| Запрос | Пустой кабинет (rows=0) | Кабинет с данными (79–100 строк) |
|---|---|---|
| `GET /me` | 449 / 822 / 1340 | — |
| `GET /me/adaccounts` (19) | 813 / 902 / 1023 | — |
| `insights` level=account/today | 1069 / 1182 / 1750 | — |
| `insights` level=ad/today | 572 / 621 / 789 | **2192 / 2411 / 2489** (79 ad) |
| `insights` level=ad/last_7d | 547 / 632 / 702 | **3936 / 4412 / 4941** (100 ad) |
| `GET /act_/campaigns` | 557 / 639 / 670 | 706 / 824 / 944 (34) |
| `GET /act_/ads` | 731 / 758 / 799 | 904 / 1095 / 1434 (100) |

**Выводы (подтверждают разделение каналов §1, а не опровергают):**

1. **Простые reads (`/me`, list campaigns/ads): 0.5–1с.** gRPC + page.evaluate overhead ≈ 0.4–0.5с — это «дно» любого вызова через Vision.
2. **`insights` с серверной агрегацией: 2–4с на страницу (limit 100).** На непустом кабинете тот же `insights ad/today` медленнее в **4×** (572 → 2192 мс): Meta агрегирует actions/attribution по сотням ad на своей стороне. С пагинацией по 500+ ad — десятки секунд на полный обход.
3. **`insights ad/last_7d`: ~4с медиана, до 5с** — самый дорогой типовой запрос.

**Архитектурное следствие.** Полный observer-цикл через Marketing API = пагинация insights по всем ad = `N страниц × 2–4с` **плюс** встроенный data-freshness lag insights (данные отстают на минуты). Для sub-минутной реакции на STOP — непригодно → **DOM/Vision остаётся каналом latency-critical** (scan/disable/enable). Единичные mutations/create/budget/analytics (0.7–2с на вызов) укладываются в SLA 5–15 мин → **Marketing API подходит для latency-tolerant**. Гипотеза §1 валидирована живым замером.

---

## 1.2. ADR: канал observer — DOM, а не перехват GraphQL (решение 2026-05-29)

**Контекст.** Данные по объявлениям из Ads Manager можно получать тремя способами:
1. **DOM-парсинг** — читаем отрендеренную HTML-таблицу (`data-surface`). Формат — форматированные строки.
2. **Marketing API** — `page.evaluate(fetch(graph.facebook.com/.../insights))`. Чистый JSON, официальный.
3. **Перехват внутреннего GraphQL** — ловим сетевые ответы внутренних запросов Ads Manager (`/api/graphql`).

**Решение.** Observer (latency-critical) остаётся на **DOM**. Перехват GraphQL **отклонён**. Marketing API — только latency-tolerant (аналитика/мутации).

**Обоснование:**
- **Перехват GraphQL vs Marketing API.** Оба дают чистый JSON, но внутренний GraphQL Meta построен на **persisted queries (`doc_id`)**, которые Meta ротирует молча → перехват ломается без предупреждения и без официальной поддержки. Marketing API даёт тот же JSON по стабильному документированному контракту. → перехват GraphQL — «худшее из двух миров», отклонён. (Индустрия anti-detect — Dolphin/FBTool — идёт через Marketing API fetch, не через перехват.)
- **DOM vs Marketing API для observer.** Marketing API имеет встроенный **data-freshness lag** (insights агрегируются у Meta, отстают на минуты). Требование к observer — **sub-минутная реакция на STOP** (подтверждено владельцем 2026-05-29). DOM показывает то же, что человек в UI, без lag → выбран для real-time.

**Принятые слабости DOM** (цена real-time, известны и приняты):
- зависимость от кастомного column-preset в Ads Manager (без нужных колонок парсер падает);
- зависимость от языка интерфейса (парсер привязан к названиям колонок, сейчас RU);
- виртуализация таблицы → нужен скролл для сбора всех строк (риск недосбора на длинных списках — отдельный tech-debt на робастность скролла).

**Marketing API остаётся** резервным каналом данных (fallback при деградации DOM) и основным для аналитики/истории/мутаций, где lag приемлем.

**Уточнение по act-каналу (#39, 2026-05-29).** Решение «observer = DOM» относится к **detect** (scan, оценка правил — sub-минутная реакция критична). А **act** (disable/enable) — операция latency-tolerant: задержка в секунды на отключение уже-сработавшего STOP приемлема. Поэтому act вынесен под флаг `observer_config.act_via_api` (**дефолт `True`**): `True` → авто-стоп и ручные toggle-кнопки исполняются через `meta_api_mutation pause_ad/activate_ad` (точно по `ad_id`, не зависит от позиции кнопки/скролла/виртуализации — устраняет класс DOM-промахов), `False` → DOM-клик `disable_worker`/`enable_worker` (спящий резерв, не выпилен). Live-замер (2026-05-29): 48 mutation-операций enable/disable, 0 промахов. Detect остаётся DOM в обоих режимах. **Важно:** и DOM, и API идут через одну Vision-сессию, поэтому DOM-резерв страхует не падение Vision (там лягут оба канала), а узкий случай сбоя Graph API на живом браузере (rate-limit / протухший Graph-токен). FSM-консистентность гарантирует `core/meta_api/fsm_sync.py` (meta_api_worker приводит `ad_alert_state` к результату mutation).

---

## 2. Что НЕ трогаем (остаётся как есть)

**Vision-стек полностью:**
- `services/browser-agent/` — расширяется новым сервисом, существующие `BrowserSessionService`, `ScannerService`, `CreatorService` остаются нетронутыми
- `proto/v1/` — добавляется `meta_api.proto`, существующие protobuf без изменений
- `clients/python_grpc/client.py` — расширяется методами `MetaApiClient`, существующий `BrowserAgentClient` без изменений
- 5 эскалаторов DOM
- `core/browser/lock.py`, `circuit_breaker.py`
- `apps/observer_worker/`, `apps/disable_worker/`, `apps/enable_worker/`, `apps/creator_recorder/`
- `core/campaign_recorder/`, `core/creator_bridge/`

**Бизнес-ядро:**
- FSM, rule evaluator, `ScannedAdRow`, outbox-паттерн, reconciler-паттерн
- Telegram-обвязка, AI-провайдеры
- БД-модели наблюдения и алертинга
- Redis-очередь, WebSocket pubsub
- `core/fake_deposits.py` (ручной механизм, опционально дополняется AdSet.pro)
- Adaptive CPA baseline, OfferRuleStat, naming tracker
- `core/observer/disable_reconciler.py`

**Креативная фабрика:**
- `core/creatives/` (uniquify_creatives, folder_opener)
- `creo_scanner.py`, `spec_builder.py`, `naming.py`, `plan_builder.py`
- `core/campaign_creator/steps/*` (24 шага) — **остаются для Vision-creator как fallback на gambling**, где Meta может зарезать креативы через API content review

---

## 3. Архитектурные принципы для нового кода

### 3.1. MetaApiService как канал, не как замена

Новый gRPC-сервис исполняет вызовы **изнутри** активной Vision-сессии. Никаких HTTP-клиентов в Python. Никакого token storage. Никаких proxy-конфигов.

Структура (TypeScript, в `services/browser-agent/src/`):

```
services/browser-agent/src/
├── meta-api/
│   ├── service.ts          # gRPC service implementation
│   ├── client.ts           # обёртка page.evaluate(fetch(...))
│   ├── insights.ts         # типизированные методы поверх /insights
│   ├── mutations.ts        # pauseAd, activate, setBudget, duplicate
│   ├── creator.ts          # createCampaign через Batch API
│   ├── audiences.ts        # custom audiences (на будущее)
│   ├── upload.ts           # adimages + advideos chunked
│   └── errors.ts           # маппинг error_subcode → gRPC status
```

Proto-контракт `proto/v1/meta_api.proto`:

```protobuf
service MetaApiService {
  // Универсальный: для нестандартных endpoints
  rpc ExecuteGraphCall(ExecuteGraphCallRequest) returns (ExecuteGraphCallResponse);

  // Insights
  rpc GetAdInsights(GetAdInsightsRequest) returns (GetAdInsightsResponse);
  rpc GetCampaignInsights(GetCampaignInsightsRequest) returns (GetCampaignInsightsResponse);
  rpc GetAccountInsights(GetAccountInsightsRequest) returns (GetAccountInsightsResponse);

  // Структура аккаунта
  rpc ListAdAccounts(ListAdAccountsRequest) returns (ListAdAccountsResponse);
  rpc ListCampaigns(ListCampaignsRequest) returns (ListCampaignsResponse);
  rpc ListAdsets(ListAdsetsRequest) returns (ListAdsetsResponse);
  rpc ListAds(ListAdsRequest) returns (ListAdsResponse);

  // Mutations (исполняются изнутри браузера)
  rpc PauseAd(EntityIdRequest) returns (MutationResponse);
  rpc ActivateAd(EntityIdRequest) returns (MutationResponse);
  rpc PauseCampaign(EntityIdRequest) returns (MutationResponse);
  rpc ActivateCampaign(EntityIdRequest) returns (MutationResponse);
  rpc SetAdsetBudget(SetBudgetRequest) returns (MutationResponse);
  rpc DuplicateCampaign(DuplicateCampaignRequest) returns (DuplicateCampaignResponse);
  rpc BulkStatusChange(BulkStatusChangeRequest) returns (BulkStatusChangeResponse);

  // Creator (Batch API)
  rpc CreateCampaign(CreateCampaignRequest) returns (CreateCampaignResponse);
  rpc UploadImage(UploadImageRequest) returns (UploadImageResponse);
  rpc UploadVideo(stream UploadVideoChunk) returns (UploadVideoResponse);

  // Health
  rpc CheckMetaApiHealth(CheckMetaApiHealthRequest) returns (CheckMetaApiHealthResponse);
}
```

### 3.2. Python `core/meta_api/` — тонкий клиент

```
core/meta_api/
├── __init__.py
├── client.py            # MetaApiClient (gRPC wrapper)
├── schemas.py           # MetaAd, MetaInsightsRow (frozen dataclasses)
├── adapters.py          # MetaInsightsRow → ScannedAdRow
├── errors.py            # маппинг gRPC errors на доменные ошибки
├── audit.py             # запись в meta_api_audit_log
├── insights/
│   ├── fetcher.py       # высокоуровневые методы (по offer, по campaign, etc)
│   └── cache.py         # опциональный PG-cache (как AICache)
├── mutations/
│   ├── base.py
│   ├── set_budget.py
│   ├── pause_campaign.py
│   ├── activate_campaign.py
│   ├── duplicate_campaign.py
│   └── bulk_status.py
├── creator.py           # CampaignSpec → gRPC CreateCampaign
├── queue.py             # MetaApiMutationTask outbox-обёртка
└── reconciler.py        # reconcile_meta_mutation_tasks
```

**Никаких httpx**. Все вызовы — `await meta_api_grpc_client.get_ad_insights(...)`. Авторизация прозрачна — токен берётся на лету в browser-agent.

### 3.3. Изоляция от observer

**Допустимые импорты `core/meta_api/`:**
- `core/db/`, `core/models/`, `core/task_queue/`, `core/worker_utils.py`, `core/config.py`
- `core/scanner/models.py` — `ScannedAdRow` как контракт
- `clients/python_grpc/client.py` — для gRPC-вызовов

**Запрещённые импорты:**
- `core/observer/*` — смешивание контрактов
- `apps/observer_worker/*`, `apps/disable_worker/*`, `apps/enable_worker/*`

### 3.4. `ScannedAdRow` остаётся главным контрактом

`MetaApiAdRow` (frozen dataclass в `core/meta_api/schemas.py`) — собственный DTO. Преобразование в `ScannedAdRow` — через явный `adapters.py` с unit-тестами.

### 3.5. `AdSnapshot` не пишется из API напрямую

Новые колонки (опционально, для будущих use cases):
- `AdSnapshot.last_api_observed_at: DateTime | None`
- `AdSnapshot.meta_ad_status: String | None`

API пишет только в свои поля. Vision — только в свои.

### 3.6. Outbox-паттерн для всех mutations

Никаких прямых вызовов gRPC из HTTP-роутера или AI-tool. Каждая mutation:
1. Запись в `MetaApiMutationTask` (`status=PENDING`, `idempotency_key UNIQUE`)
2. Worker `apps/meta_api_worker/main.py` через `PostgresTaskQueue[MetaApiMutationTask]`
3. Worker вызывает `MetaApiClient.execute(task)` → gRPC → browser-agent → fetch внутри страницы
4. Reconciler сводит фактическое состояние

### 3.7. Draft-first для AI mutations

AI-tools никогда не вызывают gRPC напрямую. Создают `MetaApiMutationTask` со `status=DRAFT`. Юзер подтверждает кнопкой в TG. `DRAFT → PENDING` → worker исполняет. `ToolHandler.risk_level: READ_ONLY | DRAFT_REQUIRED`.

### 3.8. Webhooks не работают

Webhook subscriptions требуют Admin BM. Детект `disapproved_ads` / `account_disabled` — через observer на следующем цикле скана.

### 3.9. Vision-сессия — single point of failure

Это уже было правдой для observer/disable/enable. Теперь добавляется Marketing API. Mitigation:
- `health_watchdog` мониторит Vision-сессию (как и раньше)
- При сбое — degraded mode: API недоступен, observer/disable/enable могут не работать
- Ad Library и AdSet.pro продолжают работать (независимые каналы)
- Алерт в TG: «Vision-сессия упала, требуется проверка»

### 3.10. Никаких файлов >500 строк в новом коде

### 3.11. Attribution windows — явно

В TypeScript коде MetaApiService:
```typescript
const url = `https://graph.facebook.com/v22.0/${adAccountId}/insights?` +
  `action_attribution_windows=${JSON.stringify(["1d_click","7d_click","1d_view"])}&` +
  // ...
```

Unit-тест на отсутствие deprecated `7d_view`/`28d_view`.

---

## 4. Новые модули (структура)

### 4.1. `services/browser-agent/src/meta-api/` (TypeScript, новый сервис)

См. § 3.1.

**Ключевой метод `executeGraphCall`:**
```typescript
async function executeGraphCall(
  page: Page,
  method: 'GET' | 'POST' | 'DELETE',
  endpoint: string,
  params: Record<string, string>,
  body?: any,
): Promise<any> {
  return await page.evaluate(async (args) => {
    const token = document.documentElement.innerHTML
      .match(/EAA[A-Za-z0-9_-]{100,}/)?.[0];
    if (!token) throw new Error('ACCESS_TOKEN_NOT_FOUND_IN_PAGE');

    const url = new URL(`https://graph.facebook.com/v22.0${args.endpoint}`);
    Object.entries(args.params).forEach(([k, v]) => url.searchParams.set(k, v));
    url.searchParams.set('access_token', token);

    const r = await fetch(url.toString(), {
      method: args.method,
      credentials: 'include',  // КРИТИЧНО: использует cookies страницы
      headers: { 'Accept': 'application/json' },
      body: args.body ? JSON.stringify(args.body) : undefined,
    });

    const data = await r.json();
    if (data.error) throw new Error(`GRAPH_ERROR_${data.error.code}: ${data.error.message}`);
    return data;
  }, { method, endpoint, params, body });
}
```

### 4.2. `core/meta_api/` (Python, тонкий клиент)

См. § 3.2.

### 4.3. `core/ad_library/` (независимый канал)

**Это отдельная история** — Ad Library API работает с **App Access Token**, не привязан к user session. Для него нужно:
1. Создать свой Meta App (бесплатно, без App Review) — этот App **отдельный** от нашего основного, используется только для Ad Library
2. Получить App Access Token (`GET /oauth/access_token?client_id={APP_ID}&client_secret={APP_SECRET}&grant_type=client_credentials`)
3. Использовать с любого IP — Ad Library не привязана к session

```
core/ad_library/
├── __init__.py
├── client.py            # httpx с App Access Token (стандартный HTTP)
├── scraper.py           # GET /ads_archive с фильтрами
├── parser.py            # AI-парсинг через core/ai_assistant
├── patterns.py          # агрегация в ad_patterns
└── scheduler.py         # ежедневный обход (supervisord cron)
```

**Это единственный реальный standalone Python-клиент к Meta API в проекте.** Marketing API — через browser-agent gRPC, Ad Library — через httpx напрямую.

### 4.4. `core/adset_pro/` (независимый канал)

```
core/adset_pro/
├── __init__.py
├── client.py            # OAuth/PAT + POST /api/stats/query (стандартный httpx)
├── conversions.py       # маппинг ext_sub6 ↔ fb_ad_id, ingest в БД
└── postback.py          # FastAPI endpoint для outgoing postback
```

### 4.5. `core/ai_assistant/tools/` (пакет, замена монолита)

```
core/ai_assistant/tools/
├── __init__.py
├── registry.py          # ToolRegistry
├── base.py              # ToolHandler protocol с risk_level
├── ops/                 # существующие 4
├── meta/                # READ_ONLY tools — вызывают MetaApiClient
│   ├── get_insights.py
│   ├── find_ads.py
│   ├── get_offer_performance.py
│   ├── get_account_health.py
│   └── get_competitor_patterns.py  # из ad_library
├── drafts/              # DRAFT_REQUIRED: создают MetaApiMutationTask со status=DRAFT
│   ├── request_budget_change.py
│   ├── request_clone_campaign.py
│   ├── request_bulk_pause.py
│   └── request_create_campaign.py
└── creative/
    ├── generate_ad_copy.py
    └── analyze_creative.py
```

### 4.6. Новые воркеры

- `apps/meta_api_worker/main.py` — исполняет `MetaApiMutationTask` (PENDING → SUCCESS/FAILED). Вызывает `MetaApiClient` (gRPC к browser-agent)
- `apps/ad_library_scanner/main.py` — еженочный обход конкурентов

Webhook consumer **не нужен** — webhooks недоступны.

---

## 5. БД-схема: волны миграций

**Существенно упрощается по сравнению с v3.** Нет `fb_user_tokens` (токен не хранится), нет `meta_webhook_events` (не работает), нет `meta_ad_accounts` (один кабинет пока что).

### Волна 1 — Marketing API outbox + audit (Этап 2)

| Таблица | Назначение |
|---|---|
| `meta_api_audit_log` | Append-only. BigInteger PK. `method`, `endpoint`, `params_json` JSONB, `request_body_json` JSONB nullable, `response_status` Integer, `response_json` JSONB nullable, `duration_ms`, `initiated_by` (String), `error_code` nullable, `created_at` DateTime |
| `meta_api_mutation_tasks` | Outbox: `status` enum (DRAFT/PENDING/RUNNING/SUCCESS/FAILED/CANCELLED). `mutation_kind` (set_budget/pause/clone/create/etc), `target_id`, `payload_json` JSONB, `idempotency_key` UNIQUE, `attempt_count`, `max_attempts`, `next_retry_at`, `last_error` Text, `created_by` (String), `confirmed_by` nullable, `created_at`, `confirmed_at` nullable, `completed_at` nullable |
| `AdSnapshot.last_api_observed_at` | nullable DateTime — отметка времени API-источника (опционально для отслеживания) |
| `AdSnapshot.meta_ad_status` | nullable String — статус от API (опционально) |

### Волна 2 — Ad Library (Этап 4)

| Таблица | Назначение |
|---|---|
| `ad_library_scan_runs` | `search_query`, `country_code`, `ads_fetched`, `status`, timestamps |
| `competitor_ads` | `fb_ad_archive_id` UNIQUE, `page_id`, `page_name`, `ad_creative_body`, `media_type`, `first_seen_date`, `last_seen_date`, `country_codes` JSONB, `raw_json` JSONB |
| `ad_patterns` | `pattern_type`, `pattern_text`, `occurrence_count`, `source_ad_ids` JSONB |

### Волна 3 — AdSet.pro (Этап 6)

| Таблица | Назначение |
|---|---|
| `adsetpro_credentials` | Singleton, `api_key_encrypted` (Fernet), `postback_secret_encrypted` |
| `adsetpro_postback_events` | Inbox: BigInt PK, `received_at`, `click_id`, `fb_ad_id`, `fb_ad_fk` FK SET NULL, `event_type`, `revenue`, `currency`, `raw_json` JSONB, `is_duplicate`, `processed_at` |

### Технический долг в БД (попутно)

- Подозрительно отсутствующие индексы: `AlertEvent (ad_id, created_at)`, `AdMetricHistory (ad_id, cycle_ts DESC)`, `DisableTask (ad_id, status)`, `ScanRun (outcome, started_at)`, `OfferRuleStat (calculated_at)`
- Избыточные индексы: `DisableTask.ad_id`, `AdSnapshot.ad_id` (трёхкратное покрытие)
- JSON → JSONB для `AlertEvent.metrics_json`, `AdSnapshot.warning_rule_codes/stop_rule_codes`
- `OfferRuleConfig` (37 полей) — рассмотреть вертикальный сплит

---

## 6. AI-ассистент: расширение

Структура — см. § 4.5. Принципы:

1. Whitelist через enum на уровне JSON Schema
2. Узкие операции
3. `ToolRegistry` с диспетчером
4. Rate-limit per client_key (30/час)
5. **READ_ONLY** — исполняется сразу (gRPC к browser-agent → ответ)
6. **DRAFT_REQUIRED** — создаёт `MetaApiMutationTask` со status=DRAFT → юзер подтверждает в TG

### Промпты

В `core/ai_assistant/prompts/`. Версионирование через файлы:
- `operator_system_prompt.md`
- `analytics_prompt.md`
- `creator_nl_parser_prompt.md`
- `competitor_pattern_extraction_prompt.md`
- `ad_copy_generation_prompt.md`

---

## 7. Frontend: эволюционная миграция (Вариант B)

Не переписываем с нуля. Эволюционная миграция на TypeScript + рефакторинг god-components.

| Шаг | Описание |
|---|---|
| TS setup | `allowJs: true`, `checkJs: true` |
| `api.js → api.ts` | Первым — типизация всех запросов. Критично перед добавлением Meta API endpoint'ов |
| `utils/ → .ts` | Форматтеры, хелперы |
| Разбить `AdsPage.jsx` (1446 строк) | `AdsFiltersBar`, `AdRowExpanded`, `AdsTable` + хук `useAdsData` |
| Разбить `ScriptsPage.jsx` (1456 строк) | `CreativeUniquifyModule`, `CampaignCreateModule`, `CampaignRecordModule`, `CampaignAutoCreateModule` |
| AdsPage/HistoryPage на TanStack Query | DashboardPage — эталон |
| Унификация frontend-mini | Извлечь форматтеры, доменные константы, базовый fetch в `shared/` пакет |
| Заменить `window.confirm()` | Кастомный `<ConfirmDialog>` |
| Единый `<ToastProvider>` | Сейчас 3 разных реализации |
| `tailwind.config.js` extend | Перенести CSS custom properties в `theme.extend.colors` |
| Удалить дубль `src/shared/api.js` | Неиспользуемая копия |
| `exhaustive-deps → error` | AdsPage и HistoryPage имеют stale closures |

### Новые страницы под Marketing API

| Страница | Что показывает |
|---|---|
| **AI Chat** (полноценная) | Chat-bubble layout + sidebar с историей, контекст-выбор, кнопки быстрых действий. SSE для streaming |
| **Meta API Health** | Статус Vision-сессии (от которой зависит API), последний успешный вызов, ошибки 24ч |
| **Insights Explorer** | Breakdown по age/gender/placement — расширение AnalyticsPage |
| **Campaign Builder** | Полноэкранный wizard для `CampaignSpec` → API-creator |
| **Competitor Spy** | Ad Library API — фильтры (страна, вертикаль, keywords) |
| **Bulk Actions** | Чекбоксы на таблице AdsPage в режиме "выбор" — пакетные операции через draft-first |

---

## 8. Технический долг попутно

| Долг | Где | Что делать |
|---|---|---|
| Божественный объект | `apps/observer_worker/main.py` (2220 строк) | Извлечь `pipeline.py` |
| Repository + use-case | `core/observer/db_queries.py` (1066 строк) | Read → `core/queries/observer.py`, write → `core/observer/services/` |
| Монолит-роутер | `apps/api/routers/dashboard.py` (3052), `history.py` (1602) | Разнести по доменам |
| Дублирование regression | `RegressionGuard._raw_has_regression` + `snapshot_writer._has_cumulative_metric_regression` | Вынести в `core/observer/metrics_invariants.py` |
| Кросс-импорты политик | `disable_tasks ↔ enable_tasks ↔ observer/db_queries` | `core/policies/` |
| Singleton-таблицы | `ObserverSettings`, `TelegramSettings`, `VisionSettings` | CHECK constraint или `SystemSettings(key, value_json)` |
| Cross-cutting AI-кэш | `_explain_cache` + `AICache` | `core/cache/` |

---

## 9. Этапы и чекпоинты

### Этап 0 — Подготовка (0 дней)

**Ничего не нужно.** Никакого Meta App, OAuth, scopes, Verification, token extraction наперёд. Vision уже залогинен — этого достаточно.

Опционально для Ad Library (Этап 4): создать свой Meta App для App Access Token. Это бесплатно, без App Review. Можно сделать заранее или в момент Этапа 4.

### Этап 1 — PoC + MetaApiService в browser-agent (5-7 дней)

**Что:**
- Новый proto-файл `proto/v1/meta_api.proto` с базовым `ExecuteGraphCall`
- `services/browser-agent/src/meta-api/service.ts` — реализация
- `services/browser-agent/src/meta-api/client.ts` — `executeGraphCall(page, ...)`
- Метод `GetAdInsights(ad_account_id, fields, date_preset)`
- Расширение `clients/python_grpc/client.py`: `MetaApiClient`
- PoC-скрипт `scripts/meta_api_poc.py`:
  - Через gRPC: `client.list_ad_accounts()` → получить ad_account_id
  - `client.get_ad_insights(account, level=ad, fields=[ad_id, spend, impressions, clicks, cpc, ctr], date_preset=today, limit=10)`
  - Сохранить результат
  - Сравнить с Vision-сканером на тех же объявлениях
  - Замерить latency: сколько прошло от изменения spend в реальности до того момента, как API его показал. Это требует поднимать observer параллельно и логировать diff

**Acceptance:**
- gRPC-вызов проходит чисто, возвращает insights за <2 сек
- Поля корректно маппятся в `MetaApiAdRow` → `ScannedAdRow`
- Известна latency API на твоём кабинете (среднее, p95, max за 1-2 дня наблюдения)

**Тесты:**
- Unit на `executeGraphCall` (мок Playwright page)
- Contract test: захардкоженный мок-ответ Meta → ожидаемый `MetaApiAdRow`
- Golden-file: одно объявление через Vision и через API → одинаковый `RuleEvaluation`

### Этап 2 — `core/meta_api/` Python-обвязка (4-5 дней)

**Что:**
- `core/meta_api/client.py` — `MetaApiClient` (gRPC wrapper)
- `core/meta_api/schemas.py` — frozen dataclasses
- `core/meta_api/adapters.py` — `MetaApiAdRow → ScannedAdRow`
- `core/meta_api/errors.py` — маппинг error_subcode на доменные ошибки
- `core/meta_api/audit.py` — запись в `meta_api_audit_log` (gRPC interceptor, пишет каждый вызов)
- `core/meta_api/insights/fetcher.py` — высокоуровневые методы (по offer, по campaign, etc)
- `core/meta_api/queue.py` + `core/meta_api/reconciler.py` — outbox-обвязка
- **БД миграция Волна 1**
- `apps/meta_api_worker/main.py` — пустой скелет (будет исполнять mutations на Этапе 5)

**Acceptance:**
- Insights для оффера читаются через `MetaApiClient.get_offer_performance(offer_code, since)` за <3 сек
- Audit log пишет каждый вызов с правильными полями
- Outbox-таблица создана, идемпотентность работает

**Тесты:**
- Unit на адаптеры
- Unit на errors маппинг
- Integration: gRPC → fetcher → adapter → ScannedAdRow

### Этап 3 — AI-ассистент расширение (5-6 дней)

**Что:**
- Миграция `core/ai_assistant/tools.py` → пакет с `ToolRegistry`
- 5 READ-tools: `get_insights`, `find_ads`, `get_offer_performance`, `get_account_health`, `get_competitor_patterns` (последний — заглушка до Этапа 4)
- 4 DRAFT-tools: `request_budget_change`, `request_clone_campaign`, `request_bulk_pause`, `request_create_campaign`
- 2 CREATIVE-tools: `generate_ad_copy` (заглушка), `analyze_creative`
- Промпты в `core/ai_assistant/prompts/`
- Расширение `apps/api/routers/ai.py`
- Новые TG-команды: `/ask`, `/clone`, `/budget`, `/pause_offer`
- TMA: кнопки "Подтвердить/Отклонить" под draft-tasks

**Acceptance:**
- В TG: "покажи топ-10 по spend за вчера" → AI отвечает с цифрами через gRPC к browser-agent
- "запауси все в DRC_CR2 где CPL > $20" → draft-task создан, в TG inline-кнопка
- Подтверждение → status=PENDING (worker исполнит на Этапе 5)
- AI не может исполнить mutation без подтверждения (unit-тест)

**Тесты:**
- Unit на ToolRegistry, каждый tool с моками
- Integration: ChatSession → DRAFT-tool → запись в `meta_api_mutation_tasks`
- Rate-limit per client_key

### Этап 4 — Ad Library (3-4 дня)

**Что:**
- Создать свой Meta App для App Access Token (бесплатно)
- Положить `META_AD_LIBRARY_APP_ID` и `META_AD_LIBRARY_APP_SECRET` в `.env`
- `core/ad_library/client.py` — стандартный httpx с App Access Token
- `core/ad_library/scraper.py` — `GET /ads_archive`
- `core/ad_library/parser.py` — AI-парсинг через ChatSession
- `core/ad_library/patterns.py` — агрегация в `ad_patterns`
- **БД миграция Волна 2**
- `apps/ad_library_scanner/main.py` — еженочный cron
- Frontend: страница `Competitor Spy`
- Реализация tool `get_competitor_patterns` (раньше была заглушка)

**Acceptance:**
- Cron обходит топ-10 конкурентов в наших гео+вертикалях
- AI парсит каждое объявление в JSON-структуру
- Tool `generate_ad_copy` использует топ-5 паттернов в промпте

**Тесты:**
- Unit на парсер с моками
- Snapshot tests на агрегацию
- Integration scraper → parser → DB

### Этап 5 — Mutations + API-creator (5-7 дней)

**Что:**
- `services/browser-agent/src/meta-api/mutations.ts` — реализация всех mutations через `page.evaluate(fetch)` к нужному endpoint
- `services/browser-agent/src/meta-api/creator.ts` — `CreateCampaign` через Batch API внутри page.evaluate
- `services/browser-agent/src/meta-api/upload.ts` — `UploadImage`, `UploadVideo` (chunked)
- gRPC-методы все из § 3.1
- Python-сторона: `core/meta_api/mutations/*`, `core/meta_api/creator.py`
- `apps/meta_api_worker/main.py` — полная реализация, исполняет `MetaApiMutationTask`
- `core/meta_api/reconciler.py` — `reconcile_meta_mutation_tasks` (DRAFT >24h → CANCELLED, retryable FAILED → переочередь)
- Поле `Offer.use_vision_creator: bool` для gambling fallback
- Vision-creator (`core/campaign_creator/steps/*`, `plan_runner.py`) остаётся

**Acceptance:**
- Создание тестовой кампании через API → PAUSED в Ads Manager за <10 сек
- Креативы (image+video) загружаются корректно
- Изменение бюджета через AI → draft → confirm → факт в Ads Manager
- Bulk pause 20 объявлений через Batch API в одном вызове
- `use_vision_creator=true` → старый flow работает без регрессий

**Тесты:**
- Unit на каждую mutation (мок page.evaluate)
- Integration: PlanRun → meta_api_worker → gRPC → fetch → факт в БД
- Idempotency: дважды тот же `idempotency_key` → одна задача

### Этап 6 — AdSet.pro (3 дня)

**Что:**
- `core/adset_pro/client.py` — OAuth/PAT + `POST /api/stats/query` (стандартный httpx, не через Vision)
- `core/adset_pro/conversions.py` — sync по `ext_sub6 ↔ fb_ad_id`
- `core/adset_pro/postback.py` — FastAPI endpoint для real-time outgoing postback
- **БД миграция Волна 3**
- Расширение `RuleContext` — `external_deposits` от AdSet.pro

**Acceptance:**
- Конверсии из AdSet.pro доступны в `RuleContext` рядом с `fake_deposits`
- Real-time postback приходит за <5 сек
- `fake_deposits.py` остаётся как ручной backup

**Тесты:**
- Unit на маппинг ext_sub6
- Integration: postback → DB → RuleContext

### Этап 7 — Frontend миграция (8-12 дней, параллельно с 3-6)

См. § 7. По подэтапам:
- 7.1 (2 дня): TS-setup, `api.js → api.ts`, types/
- 7.2 (2 дня): разбить AdsPage
- 7.3 (2 дня): разбить ScriptsPage
- 7.4 (1 день): AdsPage/HistoryPage на TanStack Query
- 7.5 (1 день): унификация shared/
- 7.6 (1-2 дня): ConfirmDialog, ToastProvider, tailwind extend
- 7.7 (2-3 дня): новые страницы — AI Chat, Meta API Health, Competitor Spy, Campaign Builder

### Этап 8 — Multi-account (отложен, 10-15 дней)

До второго кабинета. Добавляется `meta_ad_accounts` таблица + per-account scoping. Сложности тут меньше, чем в v3, потому что нет token storage.

### Этап 9 — Технический долг (3-5 дней, параллельно)

См. § 8.

---

## 10. Стратегия тестирования

### Существующее

- 124 unit-теста в `tests/unit/`
- 1 integration `test_observer_outcomes.py`
- API-роутер тесты через FastAPI TestClient
- `conftest.py` мокает Redis
- Vitest 4.1 + @testing-library/react 16

### Новое по этапам

| Этап | Тесты |
|---|---|
| 1 | Unit на `executeGraphCall` с мок Playwright page. Contract test: мок-ответ Meta → `MetaApiAdRow`. Golden-file: одно объявление Vision + API → identичный `RuleEvaluation` |
| 2 | Unit на адаптеры, errors. Integration: gRPC → fetcher → adapter |
| 3 | Unit на ToolRegistry, tools. Integration: ChatSession → DRAFT → DB. Rate-limit |
| 4 | Unit на парсер. Snapshot tests. Integration |
| 5 | Unit на каждую mutation. Idempotency. Integration lifecycle |
| 6 | Unit маппинг ext_sub6. Integration postback |
| 7 | Vitest для каждого нового компонента |

### Property-based

Через `hypothesis` для `adapters.py`: разные комбинации Meta API ответов → `MetaApiAdRow` не падает, инварианты соблюдаются.

---

## 11. Открытые риски и нюансы

### Технические

1. **Latency Insights** — закрыт через гибрид (Vision остаётся для real-time)
2. **Attribution silent failure** — явно прописываем `action_attribution_windows`, unit-тест
3. **API version pinning** — `META_API_VERSION=v22.0` в TypeScript browser-agent коде
4. **Custom Conversions limit 100** — счётчик + алерт на 80%

### Операционные (специфика session-tunneled requests)

5. **Vision-сессия = single point of failure для всего FB.** При сбое падают observer/disable/enable + Marketing API. Но Ad Library и AdSet.pro продолжают работать (независимые каналы). Существующий `health_watchdog` уже мониторит Vision.
6. **При смене пароля владельцем фарм-BM** — Vision разлогинится, требуется ручной re-login. То же что и сейчас.
7. **Anti-fraud Meta может soft-revoke токен сессии при подозрительной активности.** Mitigation: разумный pace API-вызовов (не bombard), batch вместо циклов, audit log для мониторинга. Не делать >100 mutations/час с одной сессии.
8. **Rate limits Marketing API.** Точные лимиты для session-tunneled токенов не задокументированы. На практике хватает на 1-2 кабинета с polling раз в 60 сек + редкие mutations. Если будем активно создавать кампании (>10/час) — нужен мониторинг.

### Стратегические

9. **Gambling content policy через API** — Meta строже фильтрует API-загрузки. Решение: `Offer.use_vision_creator: bool`
10. **Account disabled** — только детект через observer, без действий
11. **Картинки вручную, тексты через AI** — `generate_ad_copy` только тексты

### Что НЕ работает

- **Webhooks** (нужен Admin BM)
- **Standard tier rate limits** (нужен App Review + Verification)
- **System User Token** (нужен Admin BM)
- **Standalone Python-клиент Marketing API** (anti-fraud отвергает)
- Эти ограничения принимаем

---

## 12. Чёткое действие на ближайшее время

1. **Этап 1.1 — Расширение browser-agent.** Создать `proto/v1/meta_api.proto` с базовым `ExecuteGraphCall`. Реализовать `services/browser-agent/src/meta-api/client.ts` с `page.evaluate(fetch(...))`. Это минимальная вертикальная стяжка через все слои
2. **Этап 1.2 — PoC.** Из Python вызвать через gRPC `executeGraphCall(GET, /me, {}, null)` через живую Vision-сессию. Проверить, что работает (в отличие от прямого curl)
3. **Этап 1.3 — Insights call.** Получить `GET /act_X/insights?level=ad&fields=...&date_preset=today&limit=10` через тот же gRPC-путь. Замерить время, сравнить с DOM-парсером на тех же ad_id
4. **Решение по PoC.** Если работает — продолжаем по плану. Если падает — диагностируем (могут быть нюансы с CORS, credentials, scopes которые требуют другой подход)

---

## Приложение: связанные документы

- `CLAUDE.md` — общие правила и состояние кодовой базы
- Ключевые источники по архитектуре session-tunneled requests:
  - [gist dvygolov createautorules.js](https://gist.github.com/dvygolov/c2077f391bd15ba2f75d7496afb47a67) — реальный код вызова Marketing API из browser context
  - [Wevion security deep dive](https://wevion.ai/en/blog/token-cookie-facebook-ads-security/) — почему токены привязаны к session
  - [Youssef Sammouda](https://ysamm.com/uncategorized/2026/01/15/steal-dtsg-cookie.html) — security research про machine_id binding
