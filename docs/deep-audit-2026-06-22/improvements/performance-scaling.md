# Перфоманс и масштабирование — рекомендации (2026-06-22)

Аудитор: claude-sonnet-4-6. Read-only. Опирается на Фазу 1: `arch/data-layer.md`,
`arch/api-surface.md`, `arch/workers-money.md`, `findings/data-layer.md`,
`findings/api-surface.md`, `findings/workers-money.md`, `99-risk-synthesis.md`.

---

## Краткий вывод

Кодовая база в целом правильно спроектирована для текущего масштаба (один хост, одна Vision-сессия,
~12 воркер-процессов). Пул соединений asyncpg разумен (`WORKER_ENGINE_KWARGS` 2+2 per воркер vs
дефолт 5+10). gRPC-таймауты расставлены на всех RPC-методах. Главные угрозы перфомансу
прямо вытекают из находок Фазы 1:

1. **Money-баг третьего рецидива** — `naive SUM` кумулятивных `ad_metrics` в `enable_reco/analyzer.py`
   не заблокирован типами/CI; `dashboard_performance.py` и `dashboard_timeseries.py` держат inline
   DISTINCT ON вместо общего хелпера — следующий автор снова напишет `SUM(spend)`.
2. **Partition full-scan на горячем пути** — `_finish_scan_run` UPDATE по `scan_runs` без `started_at`
   (обходит все партиции на каждом скане ≈каждые 90 с); `/dashboard/stats` MAX(scan_runs) без
   `started_at ≥ NOW()-30d` (прогрессирующая деградация при накоплении месяцев данных).
3. **Шедулеры (cleanup/digest/cabinet) не имеют distributed lock** — при запуске ≥2 экземпляров
   (CI/CD rolling, k8s HPA) `cleanup_worker` может одновременно DROP/CREATE партиции + дублировать
   digest/автостарт. Сейчас один хост, но k8s-артефакты (helm/) уже в репо.
4. **`meta_api_worker` читает конфиг из БД на каждую задачу** — 2 отдельных SELECT per task без кэша;
   при всплеске очереди (autostart bulk) это N × 2 конфиг-запроса напрасной нагрузки.
5. **WebSocket: Redis pubsub-коннект per WS-соединение** — при росте числа одновременных браузеров
   каждый создаёт отдельный TCP-коннект к Redis.

Горизонтальное масштабирование **большинства** воркеров сейчас невозможно без дополнительных лидер-
локов — это намеренная архитектура (один Vision-профиль, один browser-agent), и форсировать её
не нужно. Но cleanup/digest/cabinet_scheduler при ≥2 репликах создают реальный риск double-run.

---

## Сводная таблица рекомендаций

| # | Рекомендация | Усилие | Риск | Вердикт |
|---|---|:---:|:---:|:---:|
| P1 | CI grep-guard против `naive SUM` кумулятива | S | low | **do** |
| P2 | Перевести inline DISTINCT ON на хелпер `metric_aggregation` | S | low | **do** |
| P3 | Починить `enable_reco/analyzer._aggregate_spend` → latest spend | S | low | **do** |
| P4 | Добавить `started_at` в `_finish_scan_run` WHERE (partition pruning) | S | low | **do** |
| P5 | Добавить `started_at ≥ NOW()-30d` в dashboard_stats scan_runs | S | low | **do** |
| P6 | Redis SET NX distributed lock для cleanup/digest/cabinet_scheduler | M | med | **do** |
| P7 | Короткий TTL-кэш конфига в `meta_api_worker` (1-3 с) | S | low | **consider** |
| P8 | Shared Redis pubsub-коннект в WebSocket (fan-out в памяти) | M | med | **consider** |
| P9 | Таймаут на `ChatSession.ask()` в `/ai/analyze` | S | low | **do** |
| P10 | Горизонтальное масштабирование observer/meta_api_worker | L | high | **skip** |

---

## Детализация рекомендаций

---

### P1 — CI grep-guard против naive SUM кумулятива (do / S / low)

**Почему.** Naive SUM кумулятивных `ad_metrics` — третий рецидив money-бага (CRIT-1 Round 10,
R2 99-risk-synthesis). Правило «не суммируй spend напрямую» держится комментариями и code-review,
а не автоматически. Прецедент: баг прошёл сквозь 974 теста и 12+ code-review до аудита.
Нашёл в Фазе 1: `findings/data-layer.md` §L4, `99-risk-synthesis.md` паттерн №1.

**Что делать.** Добавить step в `.github/workflows/deploy.yml` после `ruff check` (перед pytest):

```yaml
- name: Проверка — запрет naive SUM по кумулятивным метрикам
  run: |
    # SUM(spend/impressions/clicks) без предшествующего DISTINCT ON или latest_per_ad
    # в том же файле = признак money-бага. Разрешённые паттерны всегда содержат
    # DISTINCT ON или вызов latest_per_ad_*_cte.
    if grep -rn --include="*.py" \
        -E "SUM\s*\(\s*(m\.|pad\.|metrics\.|)spend" \
        apps/ core/ \
        | grep -v "DISTINCT ON" \
        | grep -v "latest_per_ad" \
        | grep -v "metric_aggregation" \
        | grep -v "# allow-naive-sum" \
        | grep -v "enable_reco/analyzer"; then  # временный allowlist до P3
      echo "FAIL: Обнаружен naive SUM по кумулятивным метрикам без DISTINCT ON/latest_per_ad"
      exit 1
    fi
```

Аналогичный guard для запросов к партиционированным таблицам без ключа:

```yaml
- name: Проверка — partition key обязателен в WHERE
  run: |
    # Запросы к ad_metrics без cycle_ts, к alert_events без created_at,
    # к scan_runs без started_at — потенциальный full-scan.
    # Проверяем только UPDATE (INSERT через ORM, SELECT через CTE уже проверен).
    if grep -rn --include="*.py" \
        "UPDATE scan_runs" apps/ core/ \
        | grep -v "started_at"; then
      echo "FAIL: UPDATE scan_runs без started_at — нет partition pruning"
      exit 1
    fi
```

**Что даёт.** Блокирует регресс money-бага автоматически. Исторически — тот же класс ошибки
прошёл дважды до аудита. Шаг занимает <2 с в CI.

**Effort.** S (30 мин). **Риск.** low — grep ложные срабатывания подавляются комментарием
`# allow-naive-sum`.

---

### P2 — Перевести inline DISTINCT ON на хелпер metric_aggregation (do / S / low)

**Почему.** `dashboard_performance.py` (строки 54-90, 124-139) и `dashboard_timeseries.py` (строки
172-187) содержат инлайн DISTINCT ON вместо вызова `core.dashboard.metric_aggregation.latest_per_ad_*`.
Два эффекта: (а) если хелпер изменится (добавится guard от sawtooth, расширится окно) — инлайн-копии
останутся позади; (б) новый автор видит «в этом файле DISTINCT ON» и по аналогии пишет рядом
SUM без DISTINCT ON. Прецедент: именно такая визуальная аналогия дала CRIT-1 в Round 10.
Нашёл в: `findings/data-layer.md` §L4.

**Что делать.** В `dashboard_performance.py` и `dashboard_timeseries.py` заменить inline-DISTINCT ON
на вызов `latest_per_ad_per_day_cte` из `core/dashboard/metric_aggregation.py` (уже используется
в 8 других местах). Функция возвращает CTE-фрагмент — его можно вставить в WITH-блок.

Пример (`dashboard_performance.py` top campaigns):
```python
# было: inline DISTINCT ON
"""
SELECT DISTINCT ON (m.ad_id, date_trunc('day', m.cycle_ts))
  m.ad_id, m.spend ...
  FROM ad_metrics m WHERE m.cycle_ts BETWEEN :from AND :to
  ORDER BY m.ad_id, date_trunc(...), m.cycle_ts DESC
"""

# стало: через хелпер
from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
cte = latest_per_ad_per_day_cte(from_ts=window_from, to_ts=window_to)
sql = f"""
WITH pad AS ({cte})
SELECT pad.ad_id, SUM(pad.spend) ...
"""
```

**Что даёт.** Единый источник правды — изменение хелпера (добавить guard, изменить окно) автоматически
покрывает все точки. Убирает 40-50 строк дублирования. Устраняет визуальный соблазн написать
SUM рядом с правильным DISTINCT ON.

**Effort.** S (1-2 ч). **Риск.** low — механическая замена, покрытая существующими тестами.

---

### P3 — Починить `enable_reco/analyzer._aggregate_spend` → latest spend (do / S / low)

**Почему.** `_aggregate_spend` суммирует все MetricSnapshot'ы наивно (`total += m.spend`), хотя
`ad_metrics` хранит КУМУЛЯТИВНЫЕ snapshot'ы. N снимков после паузы × S вздувают `total_spend`
в Rule 1 (`total_spend <= cpa*0.5` → false-negative). Валидные объявления не получают рекомендацию
к повторному включению → упущенная выручка. Подтверждено в `99-risk-synthesis.md` §R2 (HIGH),
код `core/enable_reco/analyzer.py:77-84,137`.

**Что делать.** Заменить `_aggregate_spend` на `_latest`:

```python
# core/enable_reco/analyzer.py

def should_recommend(...):
    ...
    latest = _latest(metrics)
    # P3: spend берём из последнего снимка cabinet-day, не из суммы снимков
    total_spend = latest.spend if latest and latest.spend is not None else Decimal("0")
    ...
```

Обновить `_snapshot_summary` соответственно (передаётся `total_spend`).

Обновить тест `test_enable_reco_analyzer.py:197` — он сейчас ассертит аддитивную семантику
кумулятива (вписывает баг как ожидаемое поведение).

**Что даёт.** Закрывает HIGH из R2 risk-synthesis. Рекомендации к включению объявлений
будут выдаваться корректно → правильные enable-решения оператора.

**Effort.** S (1 ч). **Риск.** low — Rule 1 OR-условие, остальные 3 правила не затронуты;
тест нужно обновить.

---

### P4 — Добавить `started_at` в `_finish_scan_run` WHERE (do / S / low)

**Почему.** `apps/observer_worker/main.py:179-188`: UPDATE scan_runs WHERE id = :id без `started_at`.
`scan_runs` партиционирована RANGE(started_at), PK = (id, started_at). Без ограничения по
partition-key Postgres обходит все живые партиции (index scan per partition). Выполняется на
каждом scan-цикле (≈90 с), на каждый кабинет. С ростом ретеншна (текущий дефолт 90 дней →
~3 месяца → 3 партиции) деградирует линейно. Нашёл в `findings/workers-money.md` §M1 (MID,
confidence: high).

**Что делать.** Пробросить `started_at` из `_begin_scan_run` (уже знает это время) в
`_finish_scan_run`:

```python
# _begin_scan_run возвращает (scan_id, started_at)
async def _begin_scan_run(...) -> tuple[int, datetime]:
    started_at = datetime.now(timezone.utc)
    ...
    return scan_id, started_at

# _finish_scan_run принимает started_at
async def _finish_scan_run(..., started_at: datetime) -> None:
    ...
    UPDATE scan_runs
    SET finished_at = NOW(), ...
    WHERE id = :id AND started_at = :started_at  # ← добавить
```

**Что даёт.** Точечный pruning в одну партицию вместо обхода всех. На горячем write-пути
(каждые 90 с) — снижение latency UPDATE и нагрузки на shared_buffers.

**Effort.** S (30 мин). **Риск.** low — механическое добавление фильтра; тест `_begin_scan_run`
нужно обновить под новый return type.

---

### P5 — Ограничить `scan_runs` MAX запрос в dashboard_stats (do / S / low)

**Почему.** `apps/api/routers/v1/dashboard_stats.py:69-71`:
```sql
SELECT MAX(started_at) FROM scan_runs
WHERE outcome = 'success' AND finished_at IS NOT NULL
```
Нет фильтра по `started_at` → Postgres seq-scan по ВСЕМ партициям scan_runs. Вызывается при
каждом рендере дашборда и через `/dashboard/batch` (6 параллельных вызовов). При 6+ месяцах
данных — десятки миллионов строк на каждый GET главного экрана. Нашёл в `findings/api-surface.md`
§HIGH-1 (confidence: high).

**Что делать.** Добавить нижнюю границу партиционного ключа:

```sql
SELECT MAX(started_at) FROM scan_runs
WHERE outcome = 'success'
  AND finished_at IS NOT NULL
  AND started_at >= NOW() - INTERVAL '30 days'  -- ← достаточно для нормальной работы
```

COALESCE с `NOW() - INTERVAL '24 hours'` уже есть — при 0 результатах корректно вернётся дефолт.

**Что даёт.** Ограничивает scan до 1-2 партиций (текущий + предыдущий месяц) вместо всей истории.
Постоянная latency независимо от накопленных данных.

**Effort.** S (15 мин). **Риск.** low — добавление фильтра в CTE, семантика COALESCE не меняется.

---

### P6 — Distributed lock для cleanup/digest/cabinet_scheduler (do / M / med)

**Почему.** Три шедулер-воркера (cleanup, digest, cabinet_scheduler) работают по расписанию
и защищены от повторного запуска через Redis SET NX ключи — но только от ВТОРОГО ТИКА
того же экземпляра. Если запустить два экземпляра (blue/green деплой, k8s rolling update,
ручной рестарт) — оба войдут в окно одновременно. Helm-артефакты (`helm/`) уже в репо,
k8s-деплой заявлен в CLOUD_24_7_READINESS.md. Конкретные риски:

- **cleanup_worker**: DROP TABLE старых партиций + CREATE следующего месяца одновременно из
  двух инстансов → конфликт DDL, возможна потеря данных.
- **digest_scheduler**: два экземпляра гонятся за SET NX (`digest:sent:YYYY-MM-DD`) → победит
  один, но второй до проверки успеет послать дайджест (состояние гонки между проверкой и SET NX).
- **cabinet_scheduler**: два bulk activate в одну минуту → idempotency_key `autostart:{day}:activate`
  предотвращает двойную задачу, но оба экземпляра создают N тиков в одно окно → двойные
  scan-trigger, двойные observer-пробуждения.

Нашёл в сквозном анализе: `arch/workers-money.md` §cabinet_scheduler, `00-system-map.md` §4,
`findings/workers-money.md` §M2 (смежная).

**Что делать.** Реализовать leader-lock через Redis SET NX с автопродлением (паттерн «Redis
distributed lock» без Redlock — одна нода Redis, достаточно для single-instance Redis):

```python
# core/workers/leader_lock.py — новый модуль

import asyncio
import uuid
from contextlib import asynccontextmanager

LOCK_TTL_SECONDS = 30  # TTL лока
LOCK_RENEW_INTERVAL = 10  # продлеваем каждые 10 с


@asynccontextmanager
async def try_leader_lock(redis, lock_name: str, ttl: int = LOCK_TTL_SECONDS):
    """
    Пытается занять distributed lock.
    Если успешно — продлевает в фоне, при выходе снимает.
    Если нет — возвращает управление без входа в блок (is_leader=False).
    """
    token = str(uuid.uuid4())
    acquired = await redis.set(lock_name, token, nx=True, ex=ttl)
    if not acquired:
        yield False
        return

    stop = asyncio.Event()

    async def _renew():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=LOCK_RENEW_INTERVAL)
            except asyncio.TimeoutError:
                # Продлеваем только если мы всё ещё владелец (CAS через Lua)
                lua = """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('expire', KEYS[1], ARGV[2])
                else
                    return 0
                end
                """
                await redis.eval(lua, 1, lock_name, token, ttl)

    renew_task = asyncio.create_task(_renew())
    try:
        yield True
    finally:
        stop.set()
        renew_task.cancel()
        # Снимаем лок только если владелец (Lua CAS)
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        await redis.eval(lua, 1, lock_name, token)
```

Применение в `cleanup_worker/main.py`:

```python
async with try_leader_lock(redis_client, "leader:cleanup_worker") as is_leader:
    if not is_leader:
        logger.info("cleanup: не лидер, пропускаем прогон")
        continue
    await run_once(engine, media_root=_MEDIA_ROOT)
```

Аналогично для digest_scheduler и cabinet_scheduler.

**Что даёт.** При rolling deploy или случайном двойном запуске — только один экземпляр
выполняет DDL/отправку/bulk-activate. Предотвращает потенциальный DDL-конфликт в cleanup
(DROP TABLE) и двойной digest в TG.

**Effort.** M (4-6 ч на модуль + тесты). **Риск.** med — новый механизм координации; при
зависании `_renew` лок может протухнуть (решается надёжным renew + SIGTERM handler). Тест:
запустить два экземпляра cleanup_worker против fakeredis, проверить что только один прогон.

**Важно:** обходить лок НЕ нужно для observer_worker и meta_api_worker — они и так выполняют
одну Vision-сессию (observer) и claim FOR UPDATE SKIP LOCKED (meta_api). Для reconciler — тоже
не нужен (идемпотентный SQL UPDATE с гвардом статуса).

---

### P7 — Короткий TTL-кэш конфига в `meta_api_worker` (consider / S / low)

**Почему.** `apps/meta_api_worker/main.py:349,374` на каждую задачу выполняет:
- `load_scanning_enabled(engine)` → SELECT observer_config
- `load_owner_tag(engine)` → SELECT observer_config

Два отдельных коннекта/SELECT per task. При всплеске очереди (autostart bulk: 50 задач за
минуту) = 100 лишних запросов к БД за минуту. Нашёл в `findings/workers-money.md` §M4 (MID).

**Что делать.** Кэш с TTL 1-3 с внутри task_loop:

```python
# apps/meta_api_worker/main.py
from time import monotonic

_config_cache: tuple[float, bool, str | None] | None = None  # (ts, scanning, owner_tag)
_CONFIG_TTL = 2.0  # секунды


async def _load_config(engine) -> tuple[bool, str | None]:
    global _config_cache
    now = monotonic()
    if _config_cache is not None and now - _config_cache[0] < _CONFIG_TTL:
        return _config_cache[1], _config_cache[2]
    scanning = await load_scanning_enabled(engine)
    owner_tag = await load_owner_tag(engine)
    _config_cache = (now, scanning, owner_tag)
    return scanning, owner_tag
```

**Что даёт.** На burst 50 задач/мин — с 100 запросов к БД до ~2-3 (один на каждые 2 с).
На idle (1-2 задачи/мин) — без изменений.

**Оговорка.** Money-настройка (scanning_enabled) применяется с задержкой до 2 с. Для
`pause_ad` асимметричного стоп-гейта это приемлемо: задержка в секунды при изменении
конфига через TG не критична (оператор нажимает кнопку, не машина). При желании нулевой
задержки — оставить как есть (verdict: consider, не do).

**Effort.** S (30 мин). **Риск.** low — только конфиг-чтение, не write-путь.

---

### P8 — Shared Redis pubsub-коннект в WebSocket (consider / M / med)

**Почему.** `apps/api/routers/ws.py:127`: каждое WS-соединение создаёт отдельный
`aioredis.from_url(...)` → отдельный TCP-коннект к Redis для pubsub. При 5 одновременных
браузерах (dashboard + mini app + мониторинг) = 5 TCP-коннектов, каждый слушает одни
и те же каналы (`fb_agent:scan:finished`, `health:updated` и др.). Не критично при текущей
нагрузке (единичные пользователи), но при добавлении алертинга команды → N операторов
одновременно.

**Что делать.** Singleton pubsub-listener в lifespan + внутрипроцессная fan-out очередь:

```python
# apps/api/main.py lifespan — запустить один pubsub listener
app.state.ws_broadcast = WsBroadcaster(redis_client, ALL_DASHBOARD_CHANNELS)
asyncio.create_task(app.state.ws_broadcast.run())

# apps/api/routers/ws.py — подписаться на in-process broadcast, не на Redis напрямую
queue = await app.state.ws_broadcast.subscribe()
try:
    async for event in queue:
        await websocket.send_json(event)
finally:
    app.state.ws_broadcast.unsubscribe(queue)
```

`WsBroadcaster` держит один Redis pubsub-коннект, при получении события копирует его
во все `asyncio.Queue` живых подписчиков.

**Что даёт.** N WS-соединений → 1 Redis pubsub-коннект вместо N. Снижает давление на Redis
при росте числа операторов.

**Оговорка.** Текущая реализация уже работает с `app.state.ws_pubsub_redis` (тест-инъекция).
Миграция нетривиальна: нужно правильно обработать старт/стоп broadcaster'а и изоляцию
state в тестах. **Verdict: consider** — при текущем числе пользователей (1-3) проблемы нет;
делать когда команда вырастет.

**Effort.** M (3-4 ч + тесты). **Риск.** med — изменение WS-архитектуры, тестируется через
имитацию нескольких соединений.

---

### P9 — Таймаут на `ChatSession.ask()` в `/ai/analyze` (do / S / low)

**Почему.** `apps/api/routers/v1/ai_analyze.py:160`: вызов AI-провайдера без таймаута.
При зависшем proxy (claudehub.fun / nekocode.app) HTTP-соединение держится открытым бесконечно.
Несколько таких запросов занимают все uvicorn event-loop слоты → FastAPI перестаёт отвечать
на /healthz → k8s перезапускает pod. Нашёл в `findings/api-surface.md` §MID-3 (confidence: high).

**Что делать.** Добавить `asyncio.wait_for`:

```python
try:
    response = await asyncio.wait_for(
        session.ask(body.prompt, block_type=body.block_type, ...),
        timeout=60.0
    )
except asyncio.TimeoutError:
    raise HTTPException(status_code=504, detail="AI-провайдер не ответил за 60 секунд")
```

**Что даёт.** Предотвращает resource leak и каскадный сбой `/healthz` при зависании AI-прокси.

**Effort.** S (15 мин). **Риск.** low — добавление try/except, не трогает логику.

---

### P10 — Горизонтальное масштабирование observer/meta_api_worker (skip)

**Почему skip.** observer_worker сейчас единственный пользователь Vision-сессии browser-agent.
Запуск второго экземпляра требует второй Vision-сессии (второго профиля, второго browser-agent)
— это не архитектурное ограничение, а ограничение лицензии/профиля. meta_api_worker при ≥2
экземплярах будет конкурировать за claim (FOR UPDATE SKIP LOCKED) — это safe по дизайну, но
один `ExecuteGraphCall` всё равно идёт через одну Vision-сессию.

**Что даёт масштабирование.** Потенциально — параллельный скан нескольких кабинетов. Сейчас
кабинеты сканируются последовательно, что при 3+ кабинетах даёт суммарный цикл 4-5 мин.

**Почему не сейчас.** Последовательный скан — сознательная архитектура (один профиль = один
набор кук = минимум anti-detect сигналов). Второй браузер-агент → второй сервер → другая
сложность. Текущий инстанс справляется с заявленным числом кабинетов (1-2).

**Verdict: skip.** Делать только если появится требование параллельного многопрофильного скана.

---

## Порядок выполнения

| Приоритет | Задачи | Обоснование |
|---|---|---|
| 1 (сейчас) | P1 + P3 + P5 + P9 | Закрывают confirmed HIGH (R2), деградацию hot-path, resource leak — каждая ≤30 мин |
| 2 (в ближайший спринт) | P2 + P4 | Тех-долг и pruning — безопасно, дают долгосрочную устойчивость |
| 3 (до k8s multi-replica) | P6 | Обязательно перед горизонтальным деплоем с ≥2 репликами шедулеров |
| 4 (по потребности) | P7 + P8 | Только при реальной нагрузке (очередь >50 задач/мин или >5 операторов) |
| — (не делать) | P10 | Нет бизнес-требования, сложность несоразмерна |
