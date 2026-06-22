# Карта архитектуры: Node.js gRPC browser-agent

Дата: 2026-06-22. Подсистема: `services/browser-agent/src/` (TS) + `clients/python_grpc/client.py` (Python-клиент).

## Назначение

Единственный «руки в браузере» компонент: исполняет всё, что требует живой Vision-сессии (anti-detect Chromium через CDP). Через него идут ДВА money-канала:

1. **Детект (scan)** — `am_tabular` метрики Ads Manager изнутри Vision-страницы (`page.evaluate(fetch)` к `adsmanager-graph.facebook.com`), без DOM/скролла. Источник данных для авто-стопа.
2. **Мутации (auto-stop / autostart)** — Marketing API (`pause_ad`/`activate_ad`/bulk/...) тем же `page.evaluate(fetch)` к `graph.facebook.com`. Канал отключения убыточной рекламы.

Архитектурный инвариант: Marketing API НИКОГДА не шлётся через httpx из Python — только `page.evaluate(fetch)` изнутри Vision-сессии (EAA-токены привязаны к session-context: machine_id/datr/fingerprint, standalone-запросы режет anti-fraud Meta).

## Компоненты

| Файл | Роль |
|------|------|
| `index.ts` | gRPC-сервер (порт 50051), регистрация 5 сервисов; хендлеры `BrowserSessionService` + `ScannerService` (runScanCycle, listCampaigns, hardReload). Точка входа `main()`. |
| `session-manager.ts` | `SessionManager` — жизненный цикл Vision-сессий: start/stop/reconnect, CDP-подключение, `ensureAdsManagerPage` (self-heal Layer 1 + мульти-кабинет резолв вкладки), `healSessionNetwork` (лечение мёртвой сети), `getPreferredSession`, поиск вкладок по act. |
| `meta-api/service.ts` | gRPC-хендлеры `MetaApiService`: `executeGraphCall`, `checkMetaApiHealth`, `uploadImage`, `uploadVideo` (client-streaming). |
| `meta-api/client.ts` | `executeGraphCall` (JSON Graph через page.evaluate), `checkMetaApiHealth` (token-only / full_probe), `runNetworkProbe`, классификация Graph error code. |
| `meta-api/upload.ts` | `uploadImage` (multipart/URL), `VideoUploadSession` (chunked resumable start/transfer/finish). |
| `am/am-fetch.ts` | Ядро скана: `extractGraphContext` (снифф токена из исходящих запросов), `acquireGraphContext` (кэш по session:act + reload-снифф + sanity-check actId), `runAmScanWithContext` (am_tabular + Graph REST edges → ScannedAdRow), `listOwnerCampaigns`, `retryTransient`. |
| `am/am-parser.ts` | Чистый парс ответов `am_tabular` (`parseAmTabular`, `mergeAmRows`) и edge-метадаты (`parseLightList`). |
| `am/am-join.ts` | `buildScannedRow` — джойн метрик+меты → `ScannedAdRow` (money-критичный маппинг spend/leads/regs/deposits, `mapEffectiveStatus`). |
| `am/am-owner.ts` | Owner-scoping матчер (зеркало Python `core/observer/queries.py`): резолв owner_tag → campaign.id. |
| `am/am-config.ts` | Параметры am_tabular: column_fields, action_types, delivery_statuses, `date_preset='today'`, limit 5000. |
| `am/am-columns-preset.ts` | QS-набор колонок Ads Manager (для UI-вкладки). |
| `page-lock.ts` | `withPageLock(sessionId, fn)` — per-session async-мьютекс (цепочка промисов) над общей `primaryPage`. |
| `session-health.ts` | Чистый детект «мёртвой сети»: `isNetworkFetchError`, `recordFetchOutcome` (счётчик серии), `shouldHealNow` (порог 2 + cooldown 45с). |
| `redis-heartbeat.ts` | Фоновый `worker:heartbeat:browser-agent` (TTL 60с, интервал 20с), ioredis с бесконечным reconnect. |
| `creator-service.ts` | `CreatorService` (RunPlan stream, StartRecording, StopRecording) — Vision-fallback для создания кампаний. |
| `ad-library/` | Ad Library pipeline (searchAds via GraphQL-сниффинг). |
| `clients/python_grpc/client.py` | `BrowserAgentClient` — Python-обёртка: circuit-breaker (3 фейла → OPEN 60с), session-recovery (NOT_FOUND → start_browser), page-recovery (reconnect), стриминг scan. |

## Последовательности вызовов

### Скан-цикл (observer → browser-agent)

```
observer_worker.main.scan_one_account(ad_account_id)
  └─ BrowserAgentClient.run_scan_cycle(campaign_ids, owner_tag, ad_account_id)
       ├─ circuit_breaker.check_open()         (OPEN → BrowserUnavailableError)
       ├─ ensure_browser_session()             (нет session_id → StartBrowser)
       └─ gRPC ScannerService.RunScanCycle(stream)
            └─ index.ts::runScanCycle
                 ├─ sessionManager.getSession(session_id)
                 ├─ actId = req.ad_account_id (strip act_)
                 ├─ ensureAdsManagerPage(session, {actId, fallbackUrl})   ← Layer 1 self-heal / резолв вкладки кабинета
                 └─ withPageLock(session_id, async () =>
                      ├─ acquireGraphContext(page, session_id, {expectedActId: actId})
                      │     ├─ cache-hit → ctx                (без reload)
                      │     └─ cache-miss → extractGraphContext(снифф) + page.reload()
                      │           └─ sanity: ctx.actId === act_<expectedActId> или throw
                      ├─ runAmScanWithContext(page, ctx, amConfig)
                      │     ├─ fetchAllEdge('campaigns')      (резолв owner_tag → campaign.id)
                      │     ├─ fetchAllAmTabular(filtering)   (метрики per-ad, retryTransient)
                      │     ├─ fetchAllEdge('ads')            (имена/статус/крео)
                      │     ├─ fetchAllEdge('adsets')         (пиксель/бюджет/learning)
                      │     ├─ enrichVideoPosters()           (best-effort постеры)
                      │     └─ buildScannedRows(merged, adMeta) → ScannedAdRow[]
                      └─ если authExpired(190) → invalidateGraphContext + re-sniff + повтор
                    )
                 ├─ call.write({complete: {all_rows: rows.map(toProtoRow), warnings, empty_reason}})
                 ├─ endIfActive()                            (call.end до heal)
                 └─ ПОСЛЕ ответа: recordFetchOutcome + shouldHealNow → healSessionNetwork
            ← stream: ScanResult → _proto_to_row → ScannedAdRow[] → process_scan_rows (Python FSM)
```

### Мутация (meta_api_worker → browser-agent)

```
meta_api_worker → dispatch_mutation → mutations/pause_ad.execute
  └─ MetaApiClient.execute_graph_call(ad_account_id, POST /{ad_id}?status=PAUSED)
       └─ gRPC MetaApiService.ExecuteGraphCall
            └─ service.ts::executeGraphCallHandler
                 ├─ resolveSession(session_id)
                 └─ withPageLock(session.id, async () =>
                      ├─ page = actId ? ensureAdsManagerPage({actId}) : getPage(session)
                      └─ executeGraphCall(page, params)
                           ├─ page.waitForFunction(/EAA.../, 10s)   ← H4-фикс: ждём токен в DOM
                           └─ page.evaluate(fetch(graph.facebook.com/<ver>/<endpoint>, credentials:'include'))
                    )
                 ├─ callback(status_code, response_json, error{code,subcode,...})
                 └─ ПОСЛЕ callback: netFail (statusCode===0) → recordFetchOutcome → healSessionNetwork
            ← Python классифицирует error.code → mark_failed / requeue → fsm_sync
```

### Health probe (health_watchdog → browser-agent)

```
health_watchdog.meta_probe_loop (раз в 300с)
  └─ MetaApiClient.check_health(full_probe=True)
       └─ MetaApiService.CheckMetaApiHealth
            └─ checkMetaApiHealthHandler
                 ├─ getPage(session)
                 └─ withPageLock(session.id, () => checkMetaApiHealth(page, {fullProbe:true}))
                      ├─ page.isClosed / url проверка / token regex
                      └─ runNetworkProbe → executeGraphCall(GET /me?fields=id)  (кэш на page, TTL 60с)
                           └─ channelDown = (code -1/-2/-3/190); rate-limit → канал жив
            ← health_details / Redis meta_api:channel:health
```

### Self-heal лесенка (внутри browser-agent)

```
session-health: netFailureStreak >= 2 && cooldown(45с) прошёл
  └─ SessionManager.healSessionNetwork(sessionId)   (под withPageLock)
       ├─ healLevel 0 → session.primaryPage.reload()
       ├─ healLevel 1 → reconnectBrowser(sessionId)               (CDP-reconnect)
       └─ healLevel 2+ → reconnectBrowser({forceProfileRestart})  (рестарт Vision-профиля — оживляет сеть)
     успех recordFetchOutcome(ok) → healLevel=0
```

## Зависимости

**Подсистема зависит от:**
- **Vision API** (`http://127.0.0.1:3030`) — старт/стоп/рестарт профилей, CDP-порт (`vision-client.ts`).
- **CDP/Playwright** — `chromium.connectOverCDP` к Vision-профилю.
- **Meta Graph / adsmanager-graph** — `page.evaluate(fetch)` (исходящий трафик из Vision-сессии).
- **Redis** (`redis://127.0.0.1:6380/0`) — только heartbeat-запись (изолировано, сбой не валит сервис).
- **proto/v1/*.proto** — runtime-загрузка через `@grpc/proto-loader` (keepCase).

**От подсистемы зависят:**
- **observer_worker** (через `BrowserAgentClient.run_scan_cycle`) — money-канал детекта.
- **meta_api_worker** (через `MetaApiClient.execute_graph_call`) — money-канал авто-стопа.
- **health_watchdog** (через `check_health`) — мониторинг живости канала.
- **creator_worker / creator_recorder** — RunPlan/Recording.
- **telegram_poller** (`/spy`) — Ad Library.

**Общие контракты:**
- `ScannedAdRow` (frozen dataclass / proto `ScannedAdRow`) — главный контракт TS↔Python. Маппинг в трёх местах: `am-join.buildScannedRow` (TS) → `index.toProtoRow` (TS→proto) → `client._proto_to_row` (proto→Python). Все три должны знать каждое поле, иначе тихий NULL (см. MEMORY: ScannedAdRow field checklist).
- Owner-scoping (`am-owner.ts`) — зеркало `core/observer/queries.py`, расхождение → неверный скоуп.
- Классификация Graph error code (TS `extractGraphError` ↔ Python `core/meta_api/errors.py`).

## Потоки данных

- **gRPC сообщения:** `RunScanCycleRequest{session_id, campaign_ids, owner_tag, ad_account_id}` → stream `ScanCycleEvent{complete{all_rows: ScannedAdRow[]}}`; `ExecuteGraphCallRequest{ad_account_id, method, endpoint, query_params, body_json}` → `ExecuteGraphCallResponse{status_code, response_json, error}`; `UploadVideoChunk` (client-stream) → `UploadVideoResponse`.
- **HTTP (внутри page.evaluate):** GET `am_tabular` (метрики), GET edges `campaigns/ads/adsets` (метадата), POST `/{ad_id}?status=` (мутация), POST `/act_X/advideos` (upload).
- **Трансформации:** сырой am_tabular JSON → `AmRow` (`parseAmTabular`) → merged `Map<adId,AmRow>` (`mergeAmRows`) → `ScannedAdRow` (`buildScannedRow`). `spend` дефолтит "0", опциональные деньги → null. `deposits ← results` (Meta «Результат»); депозиты для ПРАВИЛ берутся отдельно из AdSet.pro в Python-пайплайне.
- **Redis-ключ:** `worker:heartbeat:browser-agent` — JSON `{status, cdp_ready, cdp_port, net_fail_streak, heal_level, ts}`.
- **In-memory state:** `SessionManager.sessions` (Map по UUID), `_graphContextCache` (Map по `session:act`), `_probeCache` (WeakMap по Page), `page-lock._tails` (Map по session_id), per-session `netFailureStreak`/`healLevel`/`lastHealAt`/`primaryPage`/`lastAdsManagerUrl`.

## Внешние взаимодействия

- **Postgres:** напрямую НЕ трогает (это делает Python observer после получения rows).
- **Redis:** только heartbeat (запись). Не читает.
- **Vision:** REST-управление профилем + CDP.
- **Meta:** Graph/adsmanager-graph через браузерный fetch с `credentials:'include'` (куки сессии).
- **Telegram/AdSet.pro:** напрямую не взаимодействует.

## Инварианты и контракты (и где хрупкие)

1. **Все операции над общей `primaryPage` сериализованы `withPageLock(session.id)`** — reload (скан) и evaluate(fetch) (мутация) не пересекаются, иначе «Execution context was destroyed». Хрупко: лок per-session, а в мульти-кабинете на одну сессию приходится НЕСКОЛЬКО страниц (по вкладке на кабинет) — лок сериализует разные страницы без нужды (throughput), но это safe-сторона.
2. **`expectedActId` sanity-check** — снифф токена обязан дать запрошенный кабинет, иначе скан прерывается (защита от скана чужого кабинета). Держится в `acquireGraphContext`. Хрупко: проверка только при cache-miss (снифф); cache-hit отдаёт закэшированный ctx без сверки (но ключ кэша включает act → не перепутает).
3. **Пустой скан НЕ трогает FSM** — `process_scan_rows` зовётся только при `rows` непустых; am_tabular-ошибка → 0 rows → `outcome="empty"`, авто-стоп откладывается на цикл. Инвариант держит Python-сторона.
4. **Owner-scoping пуст → НЕ сужаем до нуля** — `owner_tag` задан, 0 кампаний матчнулось → скан без сужения (Python отфильтрует), а не «ничего».
5. **executeGraphCall никогда не бросает наружу** — все network/timeout/page-ошибки упакованы в `GraphApiCallResult.error` с code -1/-2/-3 → Python классифицирует. Хрупко: коды -1/-2/-3 — приватный контракт, рассинхрон с `errors.py` сломает маршрутизацию requeue/mark_failed.
6. **Heal эскалирует и при успехе сбрасывает healLevel** — `recordFetchOutcome(ok=true)` обнуляет streak+level. Хрупко: heal оперирует `session.primaryPage`, который в мульти-кабинете НЕ указывает на упавшую кабинетную вкладку (см. findings).
7. **Heartbeat независим от gRPC** — сбой Redis не валит сервис; пишет только при `status==='ready'`.
8. **Graceful shutdown** — SIGINT/SIGTERM → stopHeartbeat → server.tryShutdown. `keepAliveTimer` держит event loop.
