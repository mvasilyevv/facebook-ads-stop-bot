# Дизайн: реальный сетевой probe канала Marketing API (auto-stop)

Дата: 2026-06-19
Статус: согласован (решения по 2 развилкам делегированы агенту)

## Проблема

`checkMetaApiHealth` (`services/browser-agent/src/meta-api/client.ts:188`) и Python-обёртка
`MetaApiClient.check_health` (`core/meta_api/client.py:103`) проверяют только URL страницы
Ads Manager + наличие EAA-токена в DOM, но **не делают реального запроса** к
`graph.facebook.com`.

Инцидент 2026-06-19: при сетевом отказе Vision-профиля страница остаётся «healthy»
(URL верный, токен present), но реальный `page.evaluate(fetch)` падает `code=-2 Failed to
fetch`. `check_health` возвращает `healthy: true` — **false-positive**. Money-канал
auto-stop (observer → `meta_api_mutation pause_ad`) мёртв, объявления тратят бюджет, а
health_watchdog и health_details этого не видят.

Сейчас `MetaApiClient.check_health` **не вызывается нигде** в Python. Отказ канала
ловится только косвенно через БД-детектор в watchdog (коммит 841c0c05: застрявшие
`pause_ad` + рассинхрон FSM=stop_sent ↔ delivery_status=ACTIVE). Это реактивный сигнал
с лагом ≥15 мин. Нужен **проактивный** сетевой probe.

## Цель

Добавить опциональный лёгкий реальный probe к Graph API изнутри Vision-сессии, чтобы
health отражал живость **сетевого** канала, а не только наличие токена. Probe дополняет
(не заменяет) БД-детектор и CRITICAL-алерт из `core/meta_api/autostop_alert.py`.

Не-цели: переписывать БД-детектор; менять контракт `executeGraphCall`/`ScannedAdRow`;
делать probe на каждый scan-цикл.

## Решения по развилкам

1. **Архитектура** — watchdog единственный прободер → пишет результат в Redis;
   health_details читает Redis. Эндпоинт остаётся быстрым и без gRPC-зависимости;
   единственный прободер = естественный rate-limit для Meta.
2. **Probe endpoint** — реальный `GET /me?fields=id` тем же `page.evaluate(fetch)`, что
   и auto-stop `pause_ad`. Настоящий canary: ловит network-down (code -2), протухший
   токен (190) и anti-fraud отказ. Самый дешёвый аутентифицированный Graph-вызов.

## Архитектура и поток данных

```
health_watchdog (meta_probe_loop, раз в ~300с)
   └─ MetaApiClient.check_health(full_probe=True)        gRPC →
        browser-agent checkMetaApiHealth(page,{fullProbe})
           ├─ token-only проверки (URL, токен в DOM)     [как сейчас]
           └─ executeGraphCall(GET /me?fields=id)        page.evaluate(fetch) → graph
   ← probe-результат
   ├─ write Redis meta_api:channel:health (JSON, TTL ~2×interval)
   └─ network-down → CRITICAL TG-алерт (дедуп, ops-топик)

health_details (GET /health/details)
   └─ read Redis meta_api:channel:health → секция meta_api_channel + влияет на overall
```

### Уровни и интерфейсы

**1. TS `checkMetaApiHealth(page, opts?)`** — `opts: { fullProbe?: boolean; cacheTtlMs?: number }`.
- Token-only режим (без `fullProbe`) = текущее поведение, без сетевых запросов. Дёшево,
  для частых проверок.
- Full-probe режим: после успешных token-only проверок (если токена нет — probe не нужен,
  ранний возврат) делает `executeGraphCall(page, {GET /me, fields:id, timeoutMs≈8000})`.
- Кеш probe-части: module-level `WeakMap<Page, {probe, expiresAt}>`, TTL `cacheTtlMs`
  (дефолт 60000мс). На кеш-хит сетевой вызов пропускается, probe-поля берутся из кеша,
  а token/url проверяются заново (они дёшевы). Вторичный guard от частоты.
- Интерпретация probe-результата:
  - `statusCode==200 && !error` → `probe_ok=true`, `healthy=true`, `probe_detail="ok"`.
  - `error.code ∈ {-1,-2,-3}` (token-not-found / Failed to fetch / page-evaluate) →
    `probe_ok=false`, `healthy=false`, `probe_detail="probe_network_down"`.
  - `error.code==190` (OAuth, протух токен) → `probe_ok=false`, `healthy=false`,
    `probe_detail="probe_token_invalid"`.
  - иной Meta-side error (напр. rate-limit 17/4) → **канал жив** (fetch дошёл до Meta):
    `healthy=true`, `probe_ok=false`, `probe_detail` несёт код. Согласуется с
    `autostop_alert.is_channel_down_error` (rate-limit ≠ outage).

**2. proto `proto/v1/meta_api.proto`**:
- `CheckMetaApiHealthRequest`: `+ bool full_probe = 2;`
- `CheckMetaApiHealthResponse`: `+ bool probe_performed = 6; + bool probe_ok = 7;
  + int32 probe_status_code = 8; + int32 probe_duration_ms = 9; + string probe_detail = 10;`
- Регенерация: `make proto-compile` (Python в `clients/python_grpc`) + `cd
  services/browser-agent && npm run proto` (Node). pb2 коммитим (committed pb2 дрейфует).

**3. TS gRPC handler `service.ts checkMetaApiHealthHandler`**: пробрасывает `full_probe`
в `checkMetaApiHealth`; при `full_probe` оборачивает вызов в `withPageLock(session.id, …)`
(probe делает `page.evaluate(fetch)` — не должен пересекаться со scan reload). Маппит
probe-поля в ответ.

**4. Python `MetaApiClient.check_health(full_probe=False)`**: прокидывает флаг в
`CheckMetaApiHealthRequest`; добавляет probe-поля в возвращаемый dict; для full-probe
использует увеличенный gRPC-таймаут (`_HEALTH_PROBE_TIMEOUT_SECONDS=15.0` против
текущих 10.0 для token-only). `CircuitOpenError` → `probe_performed=false`,
`healthy=false`, `detail="circuit_open"`.

**5. health_watchdog `apps/health_watchdog/main.py`**:
- env `HEALTH_WATCHDOG_META_PROBE_SEC` (дефолт 300).
- `MetaApiClient` (eager-init в `main_loop` через `BROWSER_AGENT_HOST`/
  `BROWSER_AGENT_GRPC_PORT`, как meta_api_worker).
- pure `classify_meta_probe(result) -> (is_down: bool, reason: str)`: down при
  `probe_performed && !healthy` с reason из `probe_detail`/`detail`; если
  `probe_performed=false` (browser-agent недоступен/circuit) — тоже down (канал
  недостижим). Unit-тестируемо.
- `build_meta_channel_alert(reason, detail) -> str`: CRITICAL-текст в ops-топик.
- `meta_probe_loop`: раз в `META_PROBE_SEC` зовёт `check_health(full_probe=True)`,
  пишет JSON в Redis `meta_api:channel:health` (TTL = `2×META_PROBE_SEC`), при down
  шлёт алерт через существующий `_maybe_alert_with_dedup` (дедуп-ключ
  `health:alerted:meta_channel`). Best-effort: ошибки gRPC/Redis/TG не валят цикл.
  Запускается в `asyncio.gather` рядом с `heartbeat_loop`/`check_loop`. Без
  `MetaApiClient` (unit-окружение) — loop не запускается.

**6. health_details `apps/api/routers/v1/health_details.py` + schemas/health.py**:
- Новая модель `MetaApiChannelStatus`: `status: Literal["ONLINE","DEGRADED","UNKNOWN"]`,
  `healthy: bool|None`, `probe_ok: bool|None`, `detail: str|None`, `checked_at:
  datetime|None`.
- Читает Redis `meta_api:channel:health`: ключ есть+healthy → ONLINE; есть+down →
  DEGRADED; нет ключа (прободер не писал / протух) → UNKNOWN.
- `HealthDetailsResponse + meta_api_channel: MetaApiChannelStatus | None`.
- `_determine_overall`: meta-канал DEGRADED → overall не ниже DEGRADED (CRITICAL
  по-прежнему только observer OFFLINE). Соответствует формулировке задачи
  «network-down → DEGRADED/алерт». UNKNOWN overall не понижает (нет прободера ≠ отказ).

### Redis-контракт `meta_api:channel:health`

```json
{
  "healthy": true,
  "probe_performed": true,
  "probe_ok": true,
  "probe_status_code": 200,
  "probe_duration_ms": 312,
  "detail": "ok",
  "probe_detail": "ok",
  "checked_at": "2026-06-19T12:00:00+00:00"
}
```
TTL = `2 × HEALTH_WATCHDOG_META_PROBE_SEC`: если прободер (watchdog) мёртв, ключ протухает
→ health_details показывает UNKNOWN.

## Обработка ошибок

- Probe никогда не бросает наружу: TS заворачивает в `MetaApiHealthResult`; Python ловит
  `CircuitOpenError`; watchdog-loop — best-effort.
- Network-down vs Meta-side: единый критерий «канал мёртв» согласован с
  `autostop_alert.is_channel_down_error` (rate-limit/Meta-error ≠ outage; -1/-2/-3/190/
  недоступность = outage).
- health_details не зависит от browser-agent: только Redis-read. Отсутствие прободера =
  UNKNOWN, не CRITICAL.

## Тестирование

**TS (`client.test.ts`, node:test + mockPage):**
- token-only без `fullProbe` → нет сетевого вызова (mock evaluate не дёргается для fetch).
- full-probe success (200) → `probe_ok=true`, `healthy=true`.
- full-probe `Failed to fetch` (code -2) → `probe_ok=false`, `healthy=false`,
  `probe_detail=probe_network_down` (ключевой кейс инцидента: token present, но fetch падает).
- full-probe token-invalid (190) → `healthy=false`, `probe_detail=probe_token_invalid`.
- full-probe Meta rate-limit → `healthy=true`, `probe_ok=false` (канал жив).
- кеш: два full-probe подряд в пределах TTL → один реальный fetch.

**Python:**
- `MetaApiClient.check_health(full_probe=True)` — прокидывает флаг в request, парсит
  probe-поля; `full_probe=False` — флаг не выставлен, probe-поля дефолтные (fake stub).
- `classify_meta_probe` — таблица (ok/network-down/token-invalid/rate-limit/
  probe-not-performed).
- watchdog: probe down → JSON в Redis + один алерт с дедупом; probe ok → JSON «healthy»
  + нет алерта (fake MetaApiClient + fakeredis).
- health_details: Redis healthy → ONLINE и overall HEALTHY; Redis down → DEGRADED и
  overall DEGRADED; нет ключа → UNKNOWN и overall не понижен.

Комментарий над каждым тестом — по-русски, описывает сценарий (правило проекта).

## Затрагиваемые файлы

- `proto/v1/meta_api.proto` (+ pb2 регенерация в `clients/python_grpc/v1/`)
- `services/browser-agent/src/meta-api/client.ts`
- `services/browser-agent/src/meta-api/service.ts`
- `services/browser-agent/src/meta-api/client.test.ts`
- `core/meta_api/client.py`
- `apps/health_watchdog/main.py`
- `apps/api/routers/v1/health_details.py`
- `apps/api/routers/v1/schemas/health.py`
- `tests/unit/test_health_watchdog.py`, `tests/integration/test_health_watchdog_*.py`,
  `tests/integration/test_api_health_details.py`, тест для `core/meta_api/client.py`
- `CLAUDE.md` (раздел health_watchdog/health_details — после реализации)

## Развёртывание

После сборки browser-agent: `supervisorctl restart browser_agent` (под supervisord
грузится свежий dist; см. память browser-agent-restart-after-build). pb2 регенерировать
через `make proto-compile`, не полагаться на supervisorctl.
