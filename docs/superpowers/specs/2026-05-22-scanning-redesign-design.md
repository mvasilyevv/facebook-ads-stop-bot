# Redesign: цикл сканирования Observer и UI результатов

**Дата:** 2026-05-22
**Статус:** draft, ожидает ревью

## Цель

Переработать цикл сканирования Observer и отображение его результатов в UI. Текущие проблемы:

1. **«Через раз работает».** Observer ждёт `spend > 0` до 30 сек после каждого refresh, считая это признаком «загруженности данных». На спокойных объявлениях/новых сутках кабинета spend легитимно равен нулю — пишем тупой sleep на каждом цикле.
2. **«Нет подключения к браузеру» горит ложно.** UI показывает эту фразу при ЛЮБОМ `worker_status = ERROR`, а не только при реальном обрыве CDP. Любая необработанная ошибка цикла становится «нет соединения».
3. **Observer самоотключается.** При нескольких подряд пустых сканах вызывает `set_observer_scanning_enabled(False)` — пользовательский контракт «включил → должно работать» ломается.
4. **Нет видимости что именно Observer увидел.** Результат каждого цикла никуда не пишется, диагностика только в логах.

## Ключевые архитектурные решения

### 1. Готовность данных определяет browser-agent, не Observer

Текущая логика [_wait_for_data_load](apps/observer_worker/main.py:945) удаляется полностью. Observer перестаёт делать второй проход и ждать spend.

Browser-agent (Node-сервис) теперь полностью отвечает за решение «данные готовы»:
- виден ли `role=table` с ожидаемым набором колонок в хедере;
- прочитаны ли все обязательные ячейки для каждой видимой строки (включая горизонтальный скролл с per-row timeout 5 сек на ленивые колонки типа CPM, Частота);
- если в видимой строке какая-то ячейка не появилась за timeout — строка помечается как partial, но возвращается с тем что есть.

Синяя полоса загрузки в шапке Ads Manager в проверке **не участвует** — она почти всегда висит и не отражает фактической готовности (данные стримятся порциями, виртуализация подтягивает их по скроллу).

`spend = 0` — валидное значение, не индикатор «не загрузилось».

### 2. Пять явных исходов цикла

Observer трактует каждый цикл одним из исходов (`outcome`):

| Outcome | Условие | Действие observer | UI |
|---------|---------|-------------------|----|
| `OK` | rows > 0, всё прочитано | пишет snapshots/alerts, спим adaptive interval | «Сканирую» (зелёный) |
| `OK_PARTIAL` | rows > 0, есть строки с partial-колонками | пишет snapshots с тем что есть, гард в правилах пропускает строки с NULL по нужным колонкам | «Сканирую (N неполных строк)» |
| `EMPTY_OK` | rows = 0, browser-agent видит хедер таблицы — реальная пустота | спим обычный интервал (берётся из adaptive interval, threat_level=IDLE) | «Кабинет пустой / фильтр исключает всё» (серый) |
| `EMPTY_BAD` | rows = 0, browser-agent **не видит** хедер таблицы | retry 3 раза по 10 сек (counter в observer), потом TG-алерт; **остаёмся включёнными**, продолжаем retry по 60 сек | «Не вижу таблицу — проверь профиль» (жёлтый) |
| `STALE_DATA` | rows > 0, но ≥ 90% строк имеют все критические метрики = "—" | эскалация: refresh → hard reload с обходом кеша (см. ниже) | «Данные не пришли — перезагружаю с очисткой кеша (попытка N/5)» (оранжевый) |
| `BROWSER_LOST` | gRPC unavailable / disconnect / `_is_browser_connection_error` | `reconnect_browser()` с экспонентой 5→10→20→30 сек, после 5 попыток — TG-алерт, дальше продолжаем по 30 сек | «Браузер отвалился — переподключаюсь (попытка N)» (красный) |
| `INTERRUPTED` | observer упал в середине цикла (writeback не успел) | проставляется фоновой задачей API (см. секцию scan_runs) | только в истории |

**Auto-disable сканирования удаляется полностью.** Observer не вызывает `set_observer_scanning_enabled(False)` ни в одном из сценариев. Включает/выключает только пользователь.

### 3. Состояние `STALE_DATA` и эскалация

Detection (в browser-agent):
- считаем `rows_with_all_metrics_empty` (все из `impressions`, `spend`, `cpm`, `cpc`, `ctr` = "—" / пусто);
- если `rows_with_all_metrics_empty / rows_total ≥ 0.9` — пометка `stale_data=true` в `ScanResult`.

Гард (в observer):
- если у текущих `fb_ad_id` за последние сутки в `AdSnapshot` **никогда не было** не-NULL метрик — игнорируем пометку (это просто новые объявы, а не глобальный сбой).

Эскалация (в observer):

| Попытка | Действие | Пауза |
|---------|----------|-------|
| 1 | обычный `refresh()` | 15 сек |
| 2 | `hardReload(bypassCache=true)` — новый gRPC метод | 30 сек |
| 3+ | hard reload | 60 сек (cap) |

После 5 попыток подряд → TG-алерт «Ads Manager не отдаёт данные уже N минут», но **сканирование остаётся включённым**, observer продолжает hard reload каждую минуту.

Порог 0.9 настраиваемый через `ObserverSettings.stale_data_threshold` (default 0.9).

### 4. Изменения в gRPC contract (browser-agent ↔ observer)

**Новый метод:**
```
HardReload(bypass_cache: bool) → { status: ok|error, message: string }
```
Реализация в Node: `await page.reload({ waitUntil: "networkidle" }); await cdp.send("Network.clearBrowserCache")`.

**Расширения `ScanResult`:**
```
ScanResult {
    rows: [...],                           // как сейчас
    duration_seconds: float,               // как сейчас
    total_passes: int,                     // как сейчас
    phase_timings: {                       // НОВОЕ — измеряет browser-agent
        refresh_ms: int,
        first_row_ms: int,
        scroll_ms: int,
        parse_ms: int,
        total_ms: int,
    },
    // observer добавляет к phase_timings ещё eval_ms (свой замер) при записи в scan_runs
    partial_rows: [fb_ad_id, ...],         // НОВОЕ — строки, где какие-то колонки не дочитались
    warnings: [str, ...],                  // НОВОЕ — коды для UI: "loader_visible_long", "header_missing_columns", ...
    empty_reason: str | null,              // НОВОЕ — "no_active_ads" | "filter_excludes_all" | "table_not_found"
    rows_with_all_metrics_empty: int,      // НОВОЕ — для детекции STALE_DATA
}
```

## Схема БД

### Новая таблица `scan_runs`

```sql
CREATE TABLE scan_runs (
    id              BIGSERIAL PRIMARY KEY,
    scan_id         BIGINT NOT NULL,                 -- = ObserverSettings.current_scan_id
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,                     -- NULL пока цикл идёт
    outcome         TEXT NOT NULL,                   -- 'OK' | 'OK_PARTIAL' | 'EMPTY_OK' | 'EMPTY_BAD' | 'STALE_DATA' | 'BROWSER_LOST' | 'INTERRUPTED'
    rows_total      INTEGER,
    rows_partial    INTEGER,
    rows_with_data  INTEGER,                         -- хотя бы одна непустая критическая метрика
    alerts_warning  INTEGER DEFAULT 0,
    alerts_stop     INTEGER DEFAULT 0,
    phase_timings   JSONB,
    warnings        TEXT[],
    empty_reason    TEXT,
    error_kind      TEXT,                            -- 'grpc_unavailable' | 'browser_disconnect' | 'parser_missing_columns' | 'stale_data' | 'internal' | NULL
    error_message   TEXT,
    threat_level    TEXT,                            -- IDLE/LOW/MEDIUM/HIGH
    next_interval_s INTEGER
);

CREATE INDEX scan_runs_started_at_idx ON scan_runs (started_at DESC);
CREATE INDEX scan_runs_outcome_idx    ON scan_runs (outcome) WHERE outcome != 'OK';
```

Retention 30 дней: фоновый task в lifespan API раз в сутки `DELETE FROM scan_runs WHERE finished_at < now() - interval '30 days'`.

Observer вставляет «черновик» в начале цикла (`outcome='RUNNING'`, finished_at NULL), `UPDATE` в конце. Если процесс упал — фоновая задача в lifespan API (`apps/api/main.py`), запускаемая раз в 5 минут, помечает зависшие строки `outcome='INTERRUPTED'` по условию `finished_at IS NULL AND started_at < now() - interval '5 minutes'`. Эта же задача делает daily retention cleanup.

`outcome='RUNNING'` добавляется к множеству значений колонки (но не отдаётся из API — это внутренний транзит).

### Изменения в `ObserverSettings`

- Множество значений `worker_status` сужается до `{IDLE, RUNNING, WAITING_BROWSER, ERROR, PAUSED}`. PAUSED — только когда пользователь сам выключил.
- Добавляется `stale_data_threshold: NUMERIC DEFAULT 0.9` (alembic migration).
- Существующие `worker_last_error / worker_last_error_at` остаются для обратной совместимости, но основной источник правды — `scan_runs`.

## Изменения API

### `GET /api/observer/status` (расширение существующего)

Дополнить ответ:
```json
{
    // существующие поля...
    "active_phase": "scrolling" | "parsing" | "evaluating" | "sleeping" | null,
    "phase_started_at": "2026-05-22T14:23:01Z",
    "last_run": {
        "scan_id": 1247,
        "outcome": "OK",
        "started_at": "...",
        "finished_at": "...",
        "rows_total": 58,
        "rows_partial": 0,
        "rows_with_data": 47,
        "duration_seconds": 6.4,
        "threat_level": "MEDIUM",
        "warnings": [],
        "error_kind": null,
        "error_message": null
    }
}
```

### `GET /api/observer/scan-runs?limit=50&filter=all|errors|slow|with_alerts` (новый)

Возвращает список последних N циклов из `scan_runs`. Фильтры:
- `all` — все
- `errors` — `outcome NOT IN ('OK', 'OK_PARTIAL', 'EMPTY_OK')`
- `slow` — `phase_timings->>'total_ms' > 10000`
- `with_alerts` — `alerts_warning + alerts_stop > 0`

## Изменения Observer Worker

### Удаляется

- [`_wait_for_data_load`](apps/observer_worker/main.py:945) — целиком.
- [`_merge_scan_rows`](apps/observer_worker/main.py:1013) — целиком.
- Локальные константы `DATA_LOAD_POLL_INTERVAL_SECONDS`, `DATA_LOAD_MAX_WAIT_SECONDS`, `DATA_LOAD_LOG_INTERVAL_SECONDS`.
- Переменная `prev_scan_had_spend` и связанная логика.
- Все вызовы `set_observer_scanning_enabled(False)` в ветках обработки ошибок (но **не** в API endpoint и **не** в обработке пользовательского toggle).
- Глобальные переменные `_observer_status`, `_observer_message` — заменяются на запись в `scan_runs` (+ короткий summary в `ObserverSettings.worker_status/message` для совместимости).

### Добавляется

- Модуль `core/observer/scan_run_writer.py` — `begin_scan_run()`, `finish_scan_run(outcome, ...)`, `mark_interrupted(scan_id)`.
- Модуль `core/observer/outcome_classifier.py` — `classify_scan_outcome(scan_result, historical_snapshot_lookup) → ScanOutcome` (pure function, легко тестируется).
- Модуль `core/observer/stale_data_handler.py` — состояние эскалации (counter попыток), action `refresh_or_hard_reload(grpc_client, attempt)`.
- В цикле: после `run_scan_cycle` идёт `classify_scan_outcome` → switch по outcome → действие.

### Меняется

- Heartbeat-loop (есть сейчас) пишет только `worker_heartbeat_at` и `active_phase`. Не трогает `worker_status` (его меняет основной цикл).
- Reconnect-логика `BROWSER_LOST` инкапсулируется в `core/observer/browser_recovery.py` с экспоненциальной задержкой.

## Изменения Frontend

### `frontend/src/components/observer/ObserverStatusTile.jsx`

Полная переработка:
```
┌──────────────────────────────────────────────────────────────────────┐
│ Observer  ● Сканирую                            [Подробнее]  [⚙ Сутки] │
│                                                                       │
│ Фаза: парсинг строк (4.2с)                       Цикл #1247           │
│ Прогресс: ▰▰▰▰▰▱▱▱▱▱ refresh→scroll→parse→eval                       │
│                                                                       │
│ Последний цикл                                                        │
│ ┌─────────────┬──────────────┬──────────┬───────────────┐            │
│ │ Объявлений  │ С данными    │ Время    │ Угроза        │            │
│ │ 58 / 58     │ 47 (10 пустых)│ 6.4с    │ MEDIUM        │            │
│ └─────────────┴──────────────┴──────────┴───────────────┘            │
│                                                                       │
│ Следующий цикл через: 24с                       Пульс: 3с назад      │
│ Сутки кабинета: с 09:14                                              │
└──────────────────────────────────────────────────────────────────────┘
```

Маппинг статусов:
- `OK / OK_PARTIAL` → «Сканирую» (зелёный, пульсация если фаза не `sleeping`)
- `EMPTY_OK` → «Кабинет пуст» (серый)
- `EMPTY_BAD` → «Не вижу таблицу» (жёлтый)
- `STALE_DATA` → «Перезагружаю с очисткой кеша (попытка N/5)» (оранжевый)
- `BROWSER_LOST` → «Переподключаюсь к браузеру (попытка N)» (красный)
- `WAITING_BROWSER` → «Браузер занят другой задачей» (жёлтый)
- `PAUSED` → «Выключено пользователем» (серый)
- `ERROR` (internal) → короткое сообщение из `error_message` (красный)

Polling: каждые 2 сек (сейчас 5).

### `frontend/src/components/observer/ScanRunsHistoryModal.jsx` (новый)

Модалка по клику «Подробнее». Таблица последних 50 циклов с фильтрами «Все / С ошибкой / Медленные / С алертами». Клик по строке раскрывает все поля цикла (phase_timings, warnings, error_message).

### `frontend/src/components/dashboard/DashboardCommandBar.jsx`

Из [строк 117-160](frontend/src/components/dashboard/DashboardCommandBar.jsx:117) удаляется блок про observer-status (текст «Сканирую/Ожидание/Нет подключения»). Остаются только счётчики STOP/WARNING. Статус observer'а теперь живёт **только** в плитке.

### `frontend/src/components/dashboard/DashboardOperations.jsx`

Проверка «Vision/браузер подключён» в чеклисте запуска переезжает в плитку Observer (видно по бейджу). Остальные пункты чеклиста остаются.

## Тестирование

- Unit-тесты на `classify_scan_outcome` — все 7 исходов с моками `ScanResult`.
- Unit-тесты на эскалацию `STALE_DATA` — попытки 1, 2, 5, 6 (TG-алерт), counter сбрасывается при OK.
- Unit-тесты на гард «новые объявы без истории не считаются STALE_DATA».
- Integration-тест: observer запускается на mock browser-agent (gRPC stub), проходит сценарии OK / EMPTY_OK / STALE_DATA / BROWSER_LOST.
- Frontend snapshot-тест на каждый маппинг outcome → badge.

## Out of scope

- Smart-throttling Telegram-алертов (один раз на инцидент) — следующая итерация, сейчас просто rate-limit раз в 5 мин.
- WebSocket для realtime-обновления плитки — пока polling 2 сек.
- Точка-индикатор статуса в нав-баре других страниц — YAGNI, по запросу.
- Архивация `scan_runs` старше 30 дней в S3/CSV — пока просто DELETE.

## Что НЕ ломаем

- Существующие алерты в Telegram (STOP/WARNING) — формат и логика не меняются.
- FSM `AlertState` и оценка правил — не трогаем, это другой слой.
- Disable/Enable воркеры — независимы от observer.
- API `start-new-cabinet-day` — остаётся как есть.
