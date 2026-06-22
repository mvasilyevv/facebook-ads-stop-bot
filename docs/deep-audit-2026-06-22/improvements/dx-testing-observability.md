# DX, тесты, наблюдаемость — рекомендации по улучшению

**Дата:** 2026-06-22  
**Источники:** `findings/frontend-web.md`, `findings/frontend-mini.md`, `findings/workers-money.md`, `findings/data-layer.md`, `99-risk-synthesis.md`

---

## Краткий вывод

Ядро системы (race-safe claim/mark, heartbeat-контракт, partition-pruning в 11 сайтах ad_metrics) защищено **семантическими тестами** — это сильная сторона. Слабые зоны сосредоточены в трёх местах: (1) **money-класс R3** (`result['success']` для bulk-мутаций) и **R2** (enable_reco SUM vs latest) не покрыты тестами на уровне worker-pipeline; (2) **фронты** — 12 тестов на 8 экранов mini, zero-coverage интеграции `cumulativeSpendTotal` в Dashboard, и нет CI-шага для TypeScript/Vitest; (3) **наблюдаемость** — `core/metrics.py` объявлен, но большинство метрик (`scan_timer`, `WORKER_HEARTBEAT_AGE`, `DISABLE_TASKS_PENDING`) нигде не вызываются в реальном коде, а Sentry инициализирован только в `core/sentry.py` без единого вызова `setup_sentry` в entry-points воркеров.

---

## Таблица рекомендаций

| # | Рекомендация | Усилие | Риск | Вердикт |
|---|---|:---:|:---:|:---:|
| T-1 | Семантический тест: `process_one_task` + bulk `result['success']=False` → `mark_failed` | S | low | **do** |
| T-2 | Семантический тест: `_aggregate_spend` в enable_reco — многоцикловые данные | S | low | **do** |
| T-3 | Семантический тест: `spendSeries` в Dashboard использует кумулятивные значения | S | low | **do** |
| T-4 | Frontend Vitest + TypeScript `tsc --noEmit` в CI (GitHub Actions) | S | low | **do** |
| T-5 | Семантический тест: `alertStateCssVar` вместо прямой интерполяции в mini filter-chips | S | low | **do** |
| O-1 | Wire `core/metrics.py` в реальные воркеры (scan_timer, WORKER_HEARTBEAT_AGE) | M | low | **do** |
| O-2 | Money-метрики: `fb_agent_autostop_succeeded_total`, `fb_agent_task_queue_depth{type,status}` | M | low | **do** |
| O-3 | `setup_sentry()` в entry-points всех 12 воркеров + FastAPI | S | low | **do** |
| DX-1 | grep-guard в CI: `SUM(.*spend` без `DISTINCT ON` или `latest_per_ad` → fail | S | low | **do** |
| DX-2 | `pnpm gen:api` + `git diff --exit-code packages/shared/src/api/generated.ts` в CI | S | low | **do** |
| DX-3 | `core/workers/heartbeat.py` — единый модуль вместо 5 копий heartbeat_loop | S | low | **consider** |
| DX-4 | `AdSnapshotExtended` интерфейс в `@fb/shared` вместо `as AdSnapshot & {...}` кастов | S | low | **consider** |
| DX-5 | Вынести `isCplBad`/`isFreqBad` из web+mini в `@fb/shared/domain/thresholds.ts` | S | low | **consider** |
| O-4 | Grafana dashboard JSON в репо (scan_duration, autostop success/fail, outbox depth) | M | low | **consider** |
| T-6 | Partition health-check: алерт при отсутствии партиции текущего месяца | M | low | **consider** |
| DX-6 | Декомпозиция god-компонентов >500 строк (AdDrawer, AdsPage, offers/index.tsx) | L | med | **consider** |

---

## Детализация

### T-1 — Тест: bulk `result['success']=False` → `mark_failed` в worker

**Проблема (из 99-risk-synthesis.md R3):** `process_one_task` в `apps/meta_api_worker/main.py:420` после `execute_mutation` вызывает `mark_task_succeeded` безусловно — не читая `result.get('success')`. При полном отказе Meta отдать 200 с телом `{"error":...}` (batch API), bulk-стоп метится `succeeded`, деньги продолжают тратиться. Тесты в `tests/unit/test_meta_api_batch_helpers.py` и `test_meta_api_duplicate_campaign_atomic.py` проверяют `success=False` на уровне **handler**, но не на уровне **worker-pipeline** (`process_one_task` + `mark_failed`).

**Что писать:**
```python
# tests/integration/test_meta_api_outbox_e2e.py (дополнить)

async def test_bulk_all_failed_marks_task_failed_not_succeeded(
    pg_engine, clean_meta_tables, monkeypatch
):
    """Если bulk_status_change возвращает result['success']=False (все sub-requests отклонены
    Meta), worker должен вызвать mark_failed, не mark_succeeded."""
    from core.meta_api.mutations.base import success_result

    async def fake_execute(payload, client):
        # Имитируем ответ: batch OK HTTP 200, но все sub-requests провалились
        return success_result(success=False, modified_ids=[], succeeded=0, failed=2,
                              last_error="Permission denied")

    monkeypatch.setattr("apps.meta_api_worker.main.execute_mutation", fake_execute)

    task_id = await create_task(pg_engine, mutation_kind="bulk_status_change", ...)
    await claim_and_process(pg_engine, task_id)

    status = await get_task_status(pg_engine, task_id)
    assert status == "failed", f"ожидали failed, получили {status}"
```

**Benefit:** закрывает money-gap R3 — деньги тратятся вслепую при ручном bulk-pause. Тест блокирует регресс в CI.  
**Effort:** S (1-2 часа, тест в существующем файле).

---

### T-2 — Тест: enable_reco `_aggregate_spend` — многоцикловый кумулятив

**Проблема (из 99-risk-synthesis.md R2, workers-aux HIGH):** `core/enable_reco/analyzer.py:77` суммирует все `MetricSnapshot.spend` — но spend кумулятивный (нарастает за cabinet-day). Тест в `tests/unit/test_enable_reco_analyzer.py:203-206` явно ассертирует аддитивность: `assert snap["total_spend"] == "1.0"` при двух снимках по `0.5` — это фиксирует **баг**, не корректное поведение. На реальных данных, где spend за день может давать 5-10 снимков (0.5→1.0→2.0→...), Rule 1 `total_spend ≤ cpa*0.5` будет false-negative.

**Что писать:**
```python
# tests/unit/test_enable_reco_analyzer.py (дополнить)

def test_aggregate_spend_multicycle_uses_latest_not_sum():
    """_aggregate_spend с кумулятивными снимками должен брать latest.spend,
    а не суммировать снимки — иначе 1 объявление с 5 снимками даст 5x spend."""
    # Кумулятивный cabinet-day: 0.5 → 1.0 → 2.0 → 3.0 (нарастает)
    metrics = [
        _metric(minutes_ago=30, spend="0.5"),
        _metric(minutes_ago=20, spend="1.0"),
        _metric(minutes_ago=10, spend="2.0"),
        _metric(minutes_ago=5,  spend="3.0"),  # последний = актуальный
    ]
    decision = should_recommend(
        alert_state="stop_sent", snoozed_until=None, now=_now(),
        metrics=metrics,
        offer=OfferThresholds(cpa_threshold=Decimal("10")),
    )
    snap = decision.snapshot
    # Должно быть 3.0 (latest), а не 6.5 (сумма)
    assert Decimal(snap["total_spend"]) == Decimal("3.0"), (
        f"SUM кумулятива задваивает spend: {snap['total_spend']}"
    )
```

Этот тест намеренно провалится при текущем коде, сигнализируя что нужен `_latest(metrics).spend` вместо суммы. После фикса в `analyzer.py` тест станет зелёным.

**Benefit:** тест фиксирует семантический инвариант «spend = latest snapshot» и блокирует регресс того же класса что CRIT-1 Round 10 / R2 audit. Без фикса в `analyzer.py` рекомендации включения систематически подавляются для высокодоходных объявлений.  
**Effort:** S (30 минут тест + 5 строк фикс в `_aggregate_spend`).

---

### T-3 — Тест: Dashboard `spendSeries` vs `cumulativeSpendTotal`

**Проблема (из findings/frontend-web.md MID-2):** `frontend/src/routes/index.tsx:128` строит `spendSeries` как `chartQ.data.map(b => Number(b.spend ?? 0))` — прямая передача кумулятивных значений в `SpendChart`. При этом `cumulativeSpendTotal` уже существует и протестирована в `tests/data/spendTotal.test.ts`. Конфликт: headline показывает реальную сумму за день, спарклайн — раздутую.

**Что писать:**
```typescript
// frontend/src/tests/routes/dashboard.test.tsx (новый или дополнить существующий)

it("spendSeries строится через cumulativeSpendTotal, не raw.map", () => {
  // Фикстура: 3 кумулятивных бакета (растут за день, сбрасываются в полночь)
  const buckets = [
    { ts: "2026-06-22T10:00:00Z", spend: "30.0" },
    { ts: "2026-06-22T11:00:00Z", spend: "50.0" },  // cumulative
    { ts: "2026-06-22T12:00:00Z", spend: "75.0" },  // cumulative
    { ts: "2026-06-22T13:00:00Z", spend: "5.0" },   // новый cabinet-day, сброс
  ];
  // cumulativeSpendTotal ожидает 80 (75 + 5), raw.map даст сумму 160
  const correct = cumulativeSpendTotal(buckets);
  const naive = buckets.reduce((s, b) => s + Number(b.spend), 0);
  expect(correct).not.toBe(naive); // проверяем что они разные на этих данных
  // Дальше mock useChartData и проверить что компонент рендерит correct, не naive
});
```

**Benefit:** фиксирует money-класс «кумулятивные метрики нельзя суммировать» на фронте. Визуальная ошибка в спарклайне Dashboard введёт оператора в заблуждение при анализе дневного тренда.  
**Effort:** S (пишется за 1-2 часа на виджет-тест через msw/mock).

---

### T-4 — Frontend Vitest + `tsc --noEmit` в CI

**Проблема:** CI в `.github/workflows/*.yml` прогоняет только `pytest tests/`. Frontend-тесты (331 test в `frontend/`, 12 в `frontend-mini/`) и TypeScript-проверка (`tsc --noEmit`) **не запускаются в CI**. Ошибки компиляции и тестовые регрессии обнаруживаются только локально.

**Конкретные ошибки, которые CI поймает:**
- Баг CRIT frontend-web (offer delete no-op) — тест `offers.test.tsx` тестирует `OfferDeleteManager` в изоляции, а не в странице — CI выявит, что `OfferDeleteManager` не смонтирован в `OffersPage`, если добавить интеграционный тест страницы
- `tsc --noEmit` поймает unsafe casts `as AdSnapshot & {...}` при изменении `AdSnapshotOut`
- Отсутствие `pnpm gen:api` в CI означает что типы могут дрейфовать от бэкенда (см. DX-2)

**Что добавить в CI:**
```yaml
# .github/workflows/ci.yml (добавить job)
  frontend-checks:
    name: Frontend (tsc + vitest)
    runs-on: ubuntu-latest
    needs: []  # параллельно с test
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: "9" }
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm gen:api  # регенерация types перед проверкой
      - run: pnpm --filter fb-stop-bot-frontend exec tsc --noEmit
      - run: pnpm --filter fb-agent-mini exec tsc --noEmit
      - run: pnpm -r test --reporter=verbose
```

**Benefit:** TypeScript-ошибки ловятся при push. Регрессия shape-дрейфа (frontend-web MID-4) блокируется в CI. Увеличение числа тестов (T-3, T-5) сразу входит в gate.  
**Effort:** S (1-2 часа, добавление job в существующий workflow).

---

### T-5 — Тест: `alertStateCssVar` в mini filter-chips

**Проблема (из findings/frontend-mini.md MID):** `frontend-mini/src/routes/ads/index.tsx:349` использует `` `var(--fsm-${f.id})` `` — прямая интерполяция `f.id` в CSS-переменную. Баг задокументирован в комментарии `packages/shared/src/constants/states.ts:51` (токены `--fsm-warning_sent`/`--fsm-stop_sent` не существуют, только `--fsm-warning`/`--fsm-stop`). Точки фильтра рендерятся прозрачными.

Правильная функция `alertStateCssVar` уже существует в `@fb/shared` — не используется.

**Что писать:**
```typescript
// frontend-mini/src/tests/Ads.test.tsx (дополнить)

it("filter-chip использует alertStateCssVar, не прямую интерполяцию state", () => {
  // Проверяем что chip для warning_sent имеет var(--fsm-warning), не var(--fsm-warning_sent)
  render(<AdsPage />);
  const warningChip = screen.getByTestId("filter-chip-warning_sent");
  expect(warningChip).toHaveStyle({ background: "var(--fsm-warning)" });
  // НЕ: expect(warningChip).toHaveStyle({ background: "var(--fsm-warning_sent)" });
});
```

**Benefit:** визуальный дефект (прозрачные точки состояния) становится регрессионным тестом. Фикс тривиальный: заменить `` `var(--fsm-${f.id})` `` на `alertStateCssVar(f.id)` (одна строка).  
**Effort:** S (30 минут).

---

### O-1 — Wire `core/metrics.py` в реальные воркеры

**Проблема:** `core/metrics.py` объявляет 7 Prometheus-метрик (`SCAN_DURATION`, `OBSERVER_CYCLES`, `VISION_FAILURES`, `WORKER_HEARTBEAT_AGE`, `DISABLE_TASKS_PENDING`, `ENABLE_TASKS_PENDING`, `ALERT_SEND_LATENCY`). Реально используются только **2**: `VISION_FAILURES` (в `core/browser/circuit_breaker.py`) и `ALERT_SEND_LATENCY` (в `core/alerts/drain_worker.py`). Остальные 5 метрик объявлены, но нигде не вызываются — пустые нули на `GET /metrics`.

**Конкретные зазоры:**
- `scan_timer()` → не вызывается в `apps/observer_worker/main.py` (scan-цикл не измеряется)
- `OBSERVER_CYCLES.labels(outcome=...)` → не инкрементируется (нет счётчика scan success/error)
- `WORKER_HEARTBEAT_AGE.labels(worker=name).set(age)` → не заполняется (health_watchdog считает возраст heartbeat, но не публикует в Prometheus)
- `DISABLE_TASKS_PENDING` / `ENABLE_TASKS_PENDING` → не заполняются

**Минимальный план подключения:**
```python
# apps/observer_worker/main.py — wrapping scan-cycle
from core.metrics import scan_timer, OBSERVER_CYCLES

async def run_one_cycle(...):
    with scan_timer():
        try:
            result = await _do_scan(...)
            OBSERVER_CYCLES.labels(outcome="ok").inc()
        except Exception:
            OBSERVER_CYCLES.labels(outcome="error").inc()
            raise

# apps/health_watchdog/main.py — publish heartbeat age
from core.metrics import WORKER_HEARTBEAT_AGE

for worker_name, age_seconds in heartbeat_ages.items():
    WORKER_HEARTBEAT_AGE.labels(worker=worker_name).set(age_seconds)
```

**Benefit:** реальные данные на `GET /metrics` → Grafana может строить алерты на scan latency >60s и heartbeat age. Сейчас `/metrics` возвращает HTTP-request-метрики, но ничего о money-контуре.  
**Effort:** M (4-6 часов — найти все точки, добавить вызовы, проверить что не регрессия по тестам).

---

### O-2 — Money-метрики: autostop counter + outbox depth

**Проблема:** нет Prometheus-метрик, отвечающих на вопрос «сколько объявлений было остановлено автоматически» и «как глубок outbox прямо сейчас». `GET /api/v1/dashboard/stats` возвращает `pending_disable_tasks`, но это poll-эндпоинт для UI, не time-series для Grafana.

**Что добавить в `core/metrics.py`:**
```python
AUTOSTOP_MUTATIONS = Counter(
    "fb_agent_autostop_mutations_total",
    "Авто-стоп мутации через Marketing API",
    labelnames=("outcome",),  # ok / failed / requeued
)

TASK_QUEUE_DEPTH = Gauge(
    "fb_agent_task_queue_depth",
    "Глубина outbox task_queue по типу и статусу",
    labelnames=("task_type", "status"),  # meta_api_mutation/pending, etc.
)
```

`AUTOSTOP_MUTATIONS.labels(outcome="ok").inc()` — в `apps/meta_api_worker/main.py` после `mark_task_succeeded` для `pause_ad`.

`TASK_QUEUE_DEPTH` — обновлять раз в N секунд из health_watchdog (уже делает опрос БД) или отдельным коллектором.

**Benefit:** Grafana alert «autostop failed >3 за 5 минут» (деградация канала авто-стопа до появления channel-down DM) + «outbox depth >20 pending » (очередь не разбирается). Это то, что сейчас только в TG-алертах — не в метриках.  
**Effort:** M (объявление метрик + 2-3 точки вызова + Grafana panel).

---

### O-3 — `setup_sentry()` в entry-points всех воркеров

**Проблема:** `core/sentry.py` содержит `setup_sentry()`, `core/config.py` содержит `sentry_dsn` и `sentry_environment`. Ни один `run_*.py` (entry-point воркеров) и `apps/api/main.py` **не вызывают** `setup_sentry`. Sentry не инициализирован → исключения в production не попадают в Sentry, даже если `SENTRY_DSN` задан в `.env`.

**Что добавить (шаблон для каждого `run_*.py`):**
```python
# run_observer_worker.py — добавить 4 строки
from core.config import get_settings
from core.sentry import setup_sentry

if __name__ == "__main__":
    s = get_settings()
    if s.sentry_dsn:
        setup_sentry(s.sentry_dsn, environment=s.sentry_environment)
    ...
```

В `apps/api/main.py` — в `create_app()` lifespan или в `run_api.py`.

**Benefit:** при `SENTRY_DSN` в `.env` все необработанные исключения (включая money-баги R1-R5 и неизвестные) попадают в Sentry автоматически с полным stacktrace, без изменения кода воркеров.  
**Effort:** S (12 entry-points × 4 строки = 1 час).

---

### DX-1 — grep-guard в CI: запрет naive SUM(spend)

**Проблема (из findings/data-layer.md L4):** правило «не суммировать кумулятивные ad_metrics» держится только комментариями и code-review. Было три рецидива одного класса (CRIT-1 Round 10, R2 enable_reco, MID-2 Dashboard). Grep-guard механически блокирует регресс.

**Что добавить в CI:**
```yaml
# .github/workflows/ci.yml
- name: Grep-guard — запрет naive SUM(spend) без DISTINCT ON или latest_per_ad
  run: |
    # Ищем SUM(.*spend в Python-файлах без DISTINCT ON / latest_per_ad в том же файле
    VIOLATIONS=$(grep -rn --include="*.py" "SUM(.*spend" apps/ core/ | \
      while IFS=: read file line text; do
        if ! grep -q "DISTINCT ON\|latest_per_ad" "$file"; then
          echo "$file:$line: $text"
        fi
      done)
    if [ -n "$VIOLATIONS" ]; then
      echo "FAIL: naive SUM(spend) без DISTINCT ON или latest_per_ad:"
      echo "$VIOLATIONS"
      exit 1
    fi
```

**Benefit:** регресс money-bug №1 блокируется автоматически в CI. Прецедент прошёл сквозь 974 теста (Round 10) — grep не пройдёт. Стоимость ложных срабатываний нулевая (правило точечное).  
**Effort:** S (30 минут, добавить в workflow).

---

### DX-2 — `pnpm gen:api` + `git diff` проверка в CI

**Проблема (из findings/frontend-web.md MID-4):** `packages/shared/src/api/generated.ts` (6 563 строки) генерируется из `frontend/openapi.json`. Синхронизация ручная через `pnpm gen:api`. CI не проверяет что `generated.ts` актуален относительно `openapi.json`. При добавлении полей в `AdSnapshotOut` на бэкенде (например, для фикса MID-4 unsafe casts) фронт будет работать на устаревших типах.

**Что добавить в CI:**
```yaml
# frontend-checks job (из T-4)
- name: Проверка freshness openapi → generated.ts
  run: |
    pnpm gen:api
    git diff --exit-code packages/shared/src/api/generated.ts || {
      echo "FAIL: generated.ts не совпадает с openapi.json — запусти pnpm gen:api"
      exit 1
    }
```

**Benefit:** shape-дрейф фронт↔бэкенд обнаруживается в CI при push, а не при runtime `undefined`. Покрывает сценарий: разработчик добавил поле в Pydantic-схему, но забыл `pnpm gen:api`.  
**Effort:** S (15 минут, добавить шаг в frontend-checks job из T-4).

---

### DX-3 — Единый `core/workers/heartbeat.py`

**Проблема (из findings/workers-money.md L3):** `heartbeat_loop` (SET ex=60, sleep TTL/2, try/except) скопирован в 5 воркерах с минимальными отличиями. История Round 11: 6 из 7 воркеров давали ложные «мёртв» именно из-за рассинхрона имён. Контрактный тест `test_heartbeat_contract.py` уже есть (324 строки) и защищает от имя-регрессии, но не от дублирования логики.

**Что сделать:**
```python
# core/workers/heartbeat.py (новый файл, ~30 строк)
async def run_heartbeat(
    redis: Redis, name: str, ttl: int = 60, *, stop: asyncio.Event
) -> None:
    """Бесконечный цикл: SET worker:heartbeat:{name} EX {ttl}. Останов через stop."""
    while not stop.is_set():
        try:
            await redis.set(f"worker:heartbeat:{name}", "1", ex=ttl)
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), ttl / 2)
        except asyncio.TimeoutError:
            pass
```

Замена 5 копий на `asyncio.create_task(run_heartbeat(redis, "observer", stop=stop_event))`.

**Benefit:** при изменении TTL/контракта — один файл. Но `test_heartbeat_contract.py` уже проверяет имена через writer-функцию, так что защита есть и без централизации. Поэтому вердикт consider, не do.  
**Effort:** S (2-3 часа рефакторинг + проверка тестов).

---

### DX-4 — `AdSnapshotExtended` интерфейс в `@fb/shared`

**Проблема (из findings/frontend-web.md MID-4):** три компонента используют `as AdSnapshot & { creative_thumb_url?, creative_image_url?, adset_pixel_id?, ... }` — unsafe кастом к расширению. Поля **существуют** в `build_ad_snapshot` (`snapshot.py`), но не декларированы в OpenAPI-схеме → не генерируются в `generated.ts`. TypeScript не защитит от переименования.

**Два пути (оба consider, не do — зависит от стратегии):**

1. **Добавить поля в `AdSnapshotOut`** (бэкенд) → `pnpm gen:api` → типы автоматически. Безопаснее, устраняет причину. Риск: увеличение OpenAPI-схемы на ~10 полей.

2. **`AdSnapshotExtended`** в `@fb/shared/domain/`:
```typescript
// packages/shared/src/domain/ads.ts
export interface AdSnapshotExtended extends AdSnapshot {
  creative_thumb_url?: string;
  creative_image_url?: string;
  adset_pixel_id?: string;
  adset_daily_budget?: string;
  learning_stage?: string;
}
```
Убирает `as` касты, централизует расширение. Но это второй источник правды (если поле добавят в OpenAPI — надо синхронизировать вручную).

**Benefit:** TypeScript предупредит при переименовании поля на бэкенде. Устраняет 3 unsafe cast в `AdDrawer.tsx`, `AdsTable.tsx`, `adHelpers.ts`.  
**Effort:** S (1-2 часа).

---

### DX-5 — Вынести `isCplBad`/`isFreqBad` в `@fb/shared`

**Проблема (из findings/frontend-web.md MID-1 + findings/frontend-mini.md):** пороги `cpl > 30`, `freq > 4`, `roas < 1` захардкожены в `frontend/src/components/domain/ads/adHelpers.ts` **и** продублированы в `frontend-mini/src/routes/ads/index.tsx:63` (`const cplHigh = cpl != null && cpl > 30`). Оба игнорируют per-offer `cpa_threshold` из `offer_rules`.

**Правильный фикс (двухшаговый):**

1. Краткосрочно — вынести константы в `@fb/shared/domain/thresholds.ts`:
```typescript
// Дефолтные пороги (используются если offer_rules не доступны)
export const DEFAULT_CPL_THRESHOLD = 30;
export const DEFAULT_FREQ_THRESHOLD = 4;
export const DEFAULT_ROAS_THRESHOLD = 1;

export function isCplBad(v: number, threshold = DEFAULT_CPL_THRESHOLD): boolean {
  return v > threshold;
}
```

2. Среднесрочно — `AdSnapshot` должен содержать `offer_cpa_threshold?: number` (из JOIN offer_rules в `snapshot.py`), тогда хелперы используют реальный порог.

**Benefit:** убирает дублирование web↔mini (одна точка правды), упрощает будущую переработку на per-offer пороги.  
**Effort:** S (1-2 часа вынесение + проверка что оба фронта импортируют из shared).

---

### O-4 — Grafana dashboard JSON в репо

**Проблема:** Grafana развёрнута (CLAUDE.md: `мониторинг(Grafana:3000)`), метрики есть (`fb_agent_*`), но нет декларативных dashboard-файлов в репо. Восстановление настроек Grafana после пересборки хоста — ручная работа.

**Что сделать:**
- Создать `monitoring/grafana/dashboards/fb-agent.json` с панелями:
  - `rate(fb_agent_autostop_mutations_total{outcome="ok"}[5m])` — скорость авто-стопов
  - `fb_agent_task_queue_depth{task_type="meta_api_mutation",status="pending"}` — outbox depth
  - `fb_agent_scan_duration_seconds` histogram — latency скана
  - `fb_agent_worker_heartbeat_age_seconds` — возраст heartbeat по воркеру
  - `rate(fb_agent_vision_failures_total[5m])` — Vision фейлы
- Настроить Grafana provisioning (`grafana/provisioning/dashboards/`) или импортировать JSON при деплое

**Benefit:** при инциденте «авто-стоп не работает» дежурный видит causality graph, не ищет по логам. Повторный деплой не теряет настройки мониторинга.  
**Effort:** M (4-6 часов — написать JSON, настроить provisioning, протестировать на хосте).  
**Зависимость:** требует O-1 и O-2 (без wire-up метрики будут пустыми).

---

### T-6 — Partition health-check в health_watchdog

**Проблема (из findings/data-layer.md M1):** партиции следующего месяца создаёт только `cleanup_worker` раз в сутки. Если воркер не работал в последний день месяца → на 1-е число нет партиции → `INSERT INTO ad_metrics` падает → метрики и алерты не пишутся → авто-стоп слепнет. `health_watchdog` проверяет heartbeat воркеров, но не наличие партиций.

**Что добавить в `apps/health_watchdog/main.py`:**
```python
async def check_partition_exists(engine: AsyncEngine, month: date) -> bool:
    """Проверяет наличие партиции ad_metrics_YYYY_MM для текущего месяца."""
    partition_name = f"ad_metrics_{month:%Y_%m}"
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_tables WHERE tablename = :name"),
            {"name": partition_name},
        )
        return result.fetchone() is not None

# В health check loop: при False → CRITICAL DM + Redis дедуп
```

**Benefit:** предупреждение за 0-12 часов до катастрофы вместо тихого отказа записи метрик на стыке месяца. Алерт без ручного вмешательства невозможен — именно такой сигнал нужен оператору ночью.  
**Effort:** M (3-4 часа — функция + интеграция в watchdog loop + тест).

---

### DX-6 — Декомпозиция god-компонентов

**Проблема:** нарушение правила CLAUDE.md «Никаких файлов >500 строк в новом коде»:
- `components/domain/ads/AdDrawer.tsx` — 638 строк
- `routes/ads/index.tsx` — 531 строки
- `frontend-mini/src/routes/offers/index.tsx` — 644 строки
- `frontend-mini/src/routes/settings/index.tsx` — 520 строк

Прямое следствие: 12 тестов на 8 экранов mini — тяжело тестировать компонент, если он содержит 4 зоны ответственности.

**Вердикт consider (не do):** декомпозиция не устраняет баги, а улучшает тестируемость. Приоритет ниже T-1/T-2/O-3. Начинать с `AdDrawer.tsx` (самый большой, тестируемый изолированно) и `frontend-mini/src/routes/offers/index.tsx` (644 строки, ThresholdsForm + OfferList — разные домены).

**Benefit:** каждый под-компонент тестируется в изоляции → рост покрытия mini с 12 до 20-30 тестов без дублирования setup.  
**Effort:** L (8-16 часов, риск регрессии UI при неосторожном рефакторинге state).

---

## Итоговые приоритеты

**Первый прогон (do, S-effort, max impact):**
1. T-1 + T-2 — тесты на R3 (bulk success=False) и R2 (enable_reco SUM) — закрывают money-gap тестами
2. O-3 — `setup_sentry()` в 12 entry-points — одна строка на файл, включает production error-tracking
3. T-4 + DX-1 + DX-2 — frontend CI job + grep-guard + gen:api drift — один PR, блокирует классы регрессий

**Второй прогон (consider/M-effort, системный эффект):**
4. O-1 — wire metrics в observer + watchdog → Grafana начинает видеть money-контур
5. O-2 — autostop counter + outbox depth → алерты по метрикам, не только TG
6. T-6 — partition health-check в watchdog → защита на стыке месяца
