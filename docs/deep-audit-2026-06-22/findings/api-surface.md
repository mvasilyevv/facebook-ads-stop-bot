# Аудит: FastAPI Surface (`apps/api/`)

Дата аудита: 2026-06-22  
Аудитор: claude-sonnet-4-6 (subagent)  
Скоуп: `apps/api/` — main, deps, middleware, routers/v1/*, utils/

> Известные закрытые проблемы (Round 9–11) в этот документ не включены.

---

## CRIT

### CRIT-1 — Hard-delete объявлений с висящими задачами в outbox

**Файл:** `apps/api/routers/v1/ads_admin.py:37-43`  
**Уверенность:** high

**Проблема.** `POST /dashboard/ads/bulk-delete` выполняет `DELETE FROM fb_ads WHERE fb_ad_id = ANY(:ids)`. Таблица `task_queue` не связана с `fb_ads` внешним ключом (outbox-паттерн, намеренно). Каскадное удаление затрагивает: `ad_metrics`, `alert_events`, `ad_alert_state`, `meta_api_observation`, `enable_recommendations`, `ad_deposit_correction`, `ad_auto_enable_disabled`, `tracker_aggregate` — но НЕ `task_queue`.

**Последствие.** Если на момент удаления в `task_queue` висят задачи `meta_api_mutation pause_ad` или `activate_ad` (статус `pending`/`running`/`retrying`) для удалённого объявления — `meta_api_worker` попытается их исполнить. `ExecuteGraphCall(pause_ad, target_id=<удалённый_fb_ad_id>)` скорее всего вернёт ошибку Meta API (ObjectNotFound или ad уже удалён на стороне рекламодателя). Это приводит к `mark_failed` с `attempt_count++` до `max_attempts`, после чего задача застревает в `failed`. Хуже: если объявление было удалено из UI, но ещё живёт в Meta (UI-операция ≠ удаление в Meta), `meta_api_worker` может выполнить `pause_ad` по ad_id — деньги не потеряются, но мутация применяется к объявлению, для которого в БД больше нет контекста (FSM-sync после mutation не сможет обновить `ad_alert_state` — строка уже CASCADE-удалена). Reconciler затем зависнет на этих задачах или бесконечно их requeue.

**Важность как CRIT.** Не слив денег напрямую, но: возможна мутация Meta API (pause/activate) на объявление без FSM-контекста; бесконечная requeue создаёт шум в мониторинге и скрывает реальные ошибки.

**Фикс.** Перед DELETE проверить наличие active задач:
```sql
SELECT COUNT(*) FROM task_queue
WHERE payload->>'target_id' = ANY(:ids)
  AND status IN ('draft','pending','running','retrying')
```
Если > 0 → вернуть 409 с перечнем задач. Альтернатива: отменять (status='cancelled') все active задачи перед DELETE в одной транзакции.

---

## HIGH

### HIGH-1 — `scan_runs` full-scan в горячем пути `/dashboard/stats`

**Файл:** `apps/api/routers/v1/dashboard_stats.py:66-73`  
**Уверенность:** high

**Проблема.** CTE `scope` в `_query_ad_counts()`:
```sql
SELECT COALESCE(
    (SELECT MAX(started_at) FROM scan_runs
       WHERE outcome = 'success' AND finished_at IS NOT NULL),
    NOW() - INTERVAL '24 hours'
) AS since
```
Подзапрос к `scan_runs` не имеет фильтра по partition-key (`started_at`). `scan_runs` — partitioned by `started_at` (месячные партиции). Планировщик Postgres не может pruning'овать партиции без ограничения по `started_at` → выполняет seq-scan по ВСЕМ партициям.

**Последствие.** Каждый вызов `/dashboard/stats` (а также `/dashboard/batch` через `_safe_call`) читает всю историю `scan_runs` за всё время. Этот запрос вызывается при каждом рендере главного экрана дашборда. На 6+ месяцах данных — десятки миллионов строк. Прогрессирующая деградация по мере накопления данных.

**Фикс.** Ограничить подзапрос разумным окном. Последний success-скан не может быть старше 30 дней (при нормальной работе воркера):
```sql
SELECT MAX(started_at) FROM scan_runs
WHERE outcome = 'success'
  AND finished_at IS NOT NULL
  AND started_at >= NOW() - INTERVAL '30 days'
```
Фолбэк на `NOW() - INTERVAL '24 hours'` уже есть в COALESCE, поэтому при 0 результатов за 30 дней корректно вернётся дефолт.

---

### HIGH-2 — `refresh_observer_campaigns()` навигирует живой Vision-браузер

**Файл:** `apps/api/routers/v1/settings_observer.py:223-347`  
**Уверенность:** high

**Проблема.** `POST /settings/observer/refresh-campaigns` вызывает `BrowserAgentClient.start()` (или использует существующую сессию), затем `client.navigate(ads_manager_url)` — то есть навигирует активный Vision-браузер на страницу Ads Manager. Если в момент вызова `observer_worker` выполняет scan-цикл (`RunScanCycle` gRPC-стрим), навигация сбрасывает состояние страницы и прерывает текущий скан.

**Последствие.** Потеря текущего скан-цикла, возможные ошибки FSM (scan завершён без результатов), отключения объявлений могут быть пропущены в этом цикле. В худшем случае — FSM застревает в `stop_sent` без выдачи задачи на отключение. Не прямой слив денег, но означает что убыточное объявление продолжит крутиться.

**Фикс.** Перед навигацией проверять `observer:runtime` в Redis: если `status == 'running'` → вернуть 409 `observer сейчас сканирует, повторите позже`. Альтернатива — выполнять refresh через отдельную Vision-сессию/профиль.

---

### HIGH-3 — Unbounded результат `/dashboard/spend-history` при фильтре по ad_id

**Файл:** `apps/api/routers/v1/dashboard_timeseries.py:50-90`  
**Уверенность:** med

**Проблема.** Эндпоинт `GET /dashboard/spend-history?hours=168&fb_ad_id=<id>` при наличии фильтра `fb_ad_id` выполняет запрос к `ad_metrics` (partitioned) с LIMIT 10000 только при отсутствии `fb_ad_id`. При фильтре по конкретному ad_id LIMIT не применяется:
```python
if fb_ad_id:
    # нет LIMIT
    ...WHERE cycle_ts >= :from_ts AND ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fid)
else:
    ...LIMIT 10000
```
`ad_metrics` пишется на каждом скан-цикле (каждые 30-120 с). За 168 часов = до 7 дней × 24 × 60 = ~10080 строк при интервале 60 с. При интервале 30 с — до ~20000 строк. При наличии исторических данных за несколько месяцев и cabinet-day resets — накопление ещё выше.

**Последствие.** Без LIMIT ответ может содержать десятки тысяч JSON-объектов. При одновременных запросах от нескольких клиентов — OOM-риск в FastAPI-процессе. Размер ответа не ограничен middleware (BodySizeLimitMiddleware проверяет только входящий Content-Length, не исходящий).

**Фикс.** Добавить LIMIT (например, 50000) для варианта с `fb_ad_id`. Опционально: добавить заголовок `X-Total-Count` и поддержку `?limit=`/`?offset=` для пагинации.

---

### HIGH-4 — TMA-сессии инвалидируются при ротации Fernet-ключа без предупреждения

**Файл:** `apps/api/routers/v1/tma.py:97-110`  
**Уверенность:** high

**Проблема.** `_tma_secret()` — fallback-логика:
```python
def _tma_secret(settings: Settings) -> str:
    if settings.tma_secret:
        return settings.tma_secret
    return settings.encryption_key  # Fernet-ключ
```
Если `TMA_SECRET` не задан в `.env`, подписью TMA-токенов служит `ENCRYPTION_KEY` (Fernet-ключ). При ротации Fernet-ключа (`rotate_encryption_key` в `core/crypto.py`) все активные TMA-сессии немедленно инвалидируются — `itsdangerous` не сможет верифицировать старые токены. Пользователи получают 401 без объяснений.

**Последствие.** Все пользователи TMA выходят из сессии при плановой ротации ключа. Если ротация проводится при инциденте (утечка ключа), это критично: оператор, управляющий активными отключениями через Mini App, теряет доступ.

**Фикс.** Всегда требовать явный `TMA_SECRET` (отдельный ключ, не разделять с Fernet). При `tma_secret is None` — падать с RuntimeError на старте `create_app()`, как это делается для CORS wildcard. Документировать в deployment checklist.

---

## MID

### MID-1 — Дублирование строк `cabinet_day_archives` при повторных вызовах

**Файл:** `apps/api/routers/v1/observer.py:244-251`  
**Уверенность:** high

**Проблема.** `POST /observer/start-new-cabinet-day` выполняет `INSERT` в `cabinet_day_archives` без проверки уникальности:
```python
insert_stmt = CabinetDayArchive.__table__.insert().values(
    started_at=yesterday_start,
    ...
)
await conn.execute(insert_stmt)
```
Нет `ON CONFLICT DO NOTHING` и нет предварительной проверки `WHERE started_at = :date`. При двойном нажатии кнопки в UI или повторном вызове за тот же день — дублирующая строка с идентичным `started_at`.

**Последствие.** `cabinet_day_archives` используется для определения границ cabinet-day при агрегации метрик. Дубль за один день может исказить агрегации, которые JOIN-ятся с этой таблицей. Также нарушает семантику "один архив на день".

**Фикс.** Добавить UNIQUE constraint на `started_at` в модели `CabinetDayArchive` + Alembic-миграцию, либо использовать `ON CONFLICT (started_at) DO NOTHING`. До добавления constraint — проверять наличие строки перед INSERT и возвращать 409.

---

### MID-2 — Race condition в `_get_singleton()` при первом запросе

**Файл:** `apps/api/routers/v1/settings_observer.py:63-77`  
**Уверенность:** med

**Проблема.** При холодном старте (таблица `observer_config` пуста) два одновременных запроса к `GET /settings/observer` оба пройдут `scalar()` → `None`, оба выполнят `session.add(ObserverConfig())` → `flush()`. Один получит IntegrityError на UNIQUE singleton_key, транзакция откатится → 500.

Комментарий в коде признаёт это: "race condition при первом запуске маловероятен", — но на холодном старте с параллельными health-check запросами это реально.

**Последствие.** 500 на /settings/observer при холодном старте, пока не придёт повторный запрос (который найдёт уже созданную строку). Может помешать инициализации через UI.

**Фикс.** Заменить `session.add()` на `INSERT ... ON CONFLICT (singleton_key) DO NOTHING` с повторным SELECT. Или выполнить одиночный `INSERT ... ON CONFLICT DO NOTHING` в lifespan при старте приложения.

---

### MID-3 — Нет таймаута на `ChatSession.ask()` в `/ai/analyze`

**Файл:** `apps/api/routers/v1/ai_analyze.py:159-168`  
**Уверенность:** high

**Проблема.** Вызов AI-провайдера:
```python
answer = await session.ask(body.prompt, block_type=body.block_type, ...)
```
Не имеет таймаута. FastAPI/uvicorn не устанавливает таймаут на тело обработчика. При зависшем AI-провайдере (network partition, медленный cold-start модели) HTTP-соединение держится открытым бесконечно.

**Последствие.** При concurrent-запросах к `/ai/analyze` все worker-слоты uvicorn-event-loop заняты ожидающими AI-корутинами. FastAPI перестаёт отвечать на /healthz, /readyz — k8s считает pod нездоровым и перезапускает. При высокой нагрузке — каскадный сбой.

**Фикс.**
```python
answer = await asyncio.wait_for(
    session.ask(body.prompt, ...),
    timeout=60.0
)
```
При `asyncio.TimeoutError` → HTTPException 504 Gateway Timeout.

---

### MID-4 — Silent 404 при ошибке импорта роутера

**Файл:** `apps/api/routers/v1/__init__.py:40-50`  
**Уверенность:** high

**Проблема.** `register_all()` при ошибке импорта модуля:
```python
try:
    module = importlib.import_module(f"apps.api.routers.v1.{module_info.name}")
except Exception:
    logger.exception("Не удалось загрузить роутер %s", module_info.name)
    continue
```
Роутер просто отсутствует в приложении. Все его эндпоинты возвращают 404. Никакого 500 при старте, никакого алерта — только лог-строка уровня ERROR (которую могут пропустить).

**Последствие.** После деплоя с синтаксической ошибкой в одном из роутеров часть API молча недоступна. Особенно опасно для `disable_tasks.py`, `enable_recommendations.py` — потеря возможности управлять отключениями через UI при активных инцидентах. Мониторинг не видит проблему: /healthz и /readyz возвращают 200.

**Фикс.** При ошибке импорта роутера завершать старт приложения с ошибкой (raise в lifespan или в `register_all()`), либо реализовать stub-роутер который возвращает 503 с описанием ошибки. Минимум — отправлять Telegram-алерт через `core.telegram.client` при ошибке импорта.

---

## LOW

### LOW-1 — Cache key manipulation через `scope_key` в `/ai/analyze`

**Файл:** `apps/api/routers/v1/ai_analyze.py:136`  
**Уверенность:** low

**Проблема.** Ключ кэша:
```python
cache_key = f"ai:cache:analyze:{body.block_type}:{body.scope_key}"
```
`scope_key` — произвольная строка от клиента, не санируется. Если `scope_key` содержит `:` (например, `"foo:bar"`), ключ становится `ai:cache:analyze:TYPE:foo:bar`. Это не вызывает конфликта с другими namespace'ами (у них другие префиксы), но два разных scope_key с одинаковым "развёрнутым" значением могут шарить кэш: `scope_key="a:b"` + `block_type="T"` → тот же ключ что `scope_key="b"` + `block_type="T:a"`.

**Последствие.** Теоретическая атака: авторизованный пользователь подбирает scope_key для попадания в чужой кэш-слот и получает ответ из другого контекста. На практике — низкий риск (все пользователи авторизованы, ответы AI не содержат секретных данных).

**Фикс.** Санировать `scope_key`: заменить `:` на `_` или использовать `hashlib.sha256(scope_key.encode()).hexdigest()` как компонент ключа.

---

### LOW-2 — `ads_actions.py`: desktop snooze без ACL (любой с API-key может снузить любое объявление)

**Файл:** `apps/api/routers/v1/ads_actions.py:41-77`  
**Уверенность:** high

**Проблема.** `POST /dashboard/ads/{fb_ad_id}/snooze` и `/bulk-snooze` — открыты для любого, кто передал корректный X-API-Key. Нет проверки: является ли вызывающий recipient'ом с правами на этот ad. TMA-версия в `tma.py` аналогична: любой recipient (не только owner) может снузить любое объявление.

**Последствие.** В текущей системе единственный web-ключ (X-API-Key) разделяется между всеми операторами — это осознанное допущение. Риск низкий, но явно не задокументированный security boundary.

**Фикс.** Задокументировать в security policy. При введении мультипользовательского доступа (откладывается по MEMORY) — добавить ACL на уровне роутера.

---

### LOW-3 — `PUT /settings/cabinet-autostart` не защищён от некорректного расписания

**Файл:** `apps/api/routers/v1/settings_cabinet_autostart.py:37-58`  
**Уверенность:** med

**Проблема.** `write_autostart_config()` принимает `hour_utc` и `minute_utc` из тела запроса. Валидация — только Pydantic (0-23 для часов, 0-59 для минут). Нет защиты от случайного включения в нерабочее время (например, 03:00 UTC когда бюджет ещё не загружен на следующий день). Это money-риск: автостарт включает объявления через Marketing API bulk activate — запуск в неверное время означает слив бюджета вне запланированного окна.

**Последствие.** Оператор с X-API-Key может случайно поставить автостарт на 03:00 UTC. Следующий же цикл `cabinet_scheduler` включит все объявления в 03:00. Нет механизма "это нечаянно".

**Фикс.** На уровне API: добавить предупреждение (не блокировку) если `hour_utc` вне бизнес-часов (например, 06:00-22:00 UTC). Или требовать явный флаг подтверждения `confirm_off_hours=true` для нестандартного времени. Минимум — логировать `WARNING` при сохранении времени вне типичного диапазона.

---

## Суммарная таблица

| # | Severity | Файл:строка | Проблема | Confidence |
|---|----------|------------|---------|-----------|
| 1 | CRIT | `ads_admin.py:37` | Hard-delete ads с pending task_queue (outbox orphan) | high |
| 2 | HIGH | `dashboard_stats.py:69` | scan_runs full-scan без partition filter | high |
| 3 | HIGH | `settings_observer.py:223` | refresh-campaigns навигирует live Vision во время скана | high |
| 4 | HIGH | `dashboard_timeseries.py:67` | spend-history без LIMIT при фильтре fb_ad_id | med |
| 5 | HIGH | `tma.py:97` | TMA_SECRET fallback на Fernet-ключ — ротация = потеря сессий | high |
| 6 | MID | `observer.py:244` | cabinet_day_archives INSERT без UNIQUE guard | high |
| 7 | MID | `settings_observer.py:63` | _get_singleton() race при холодном старте | med |
| 8 | MID | `ai_analyze.py:159` | Нет таймаута на ChatSession.ask() | high |
| 9 | MID | `routers/v1/__init__.py:43` | Ошибка импорта роутера → silent 404 | high |
| 10 | LOW | `ai_analyze.py:136` | scope_key в cache key без санитизации | low |
| 11 | LOW | `ads_actions.py:41` | Desktop snooze без ACL (осознанное допущение) | high |
| 12 | LOW | `settings_cabinet_autostart.py:37` | Нет защиты от случайного нерабочего времени автостарта | med |
