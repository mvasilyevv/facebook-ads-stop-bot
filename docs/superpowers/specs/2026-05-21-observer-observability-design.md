# Observer Observability: scan_id, ручной rollover суток, UI-плитка, чистка логов

**Дата:** 2026-05-21
**Статус:** Design (ожидает одобрения)
**Контекст:** На дашборде «Распределение» показывает 34 объявления вместо 42 реально активных. Пользователь не понимает, что сейчас делает observer (sleep, pending guard, partial batch). ZeroScanGuard смешивает две задачи: защита от случайных пустых батчей и автоматический переход на новые сутки кабинета. Логи observer'а зашумлены повторяющимся «сканирование отключено».

---

## 1. Цели

1. Дашборд показывает ровно то распределение, которое было в **последнем полностью принятом проходе observer**, без зависимости от окна времени.
2. Переход на новые сутки кабинета — **только ручной**, по кнопке в UI. ZeroScanGuard продолжает защищать от пустых батчей, но больше не дёргает rollover.
3. На дашборде есть **компактная плитка «Observer»**, по которой за 2 секунды видно: жив ли воркер, когда был последний скан, размер последнего батча, не висит ли в guard'е.
4. Из `observer.log` удалить периодический мусор «сканирование отключено».

## 2. Не-цели

- Не вводим общий rate-limit фреймворк для логов (YAGNI — точечно убираем один источник шума).
- Не строим отдельную страницу `/observer` с лог-стримом (пользователь выбрал компактную плитку).
- Не меняем поведение guard на пустой БД (по запросу пользователя оставляем как есть).
- Не трогаем enable/disable workers, telegram_poller, FSM алертов — фокус только на observer + dashboard.

---

## 3. Архитектура изменений

Четыре независимых, но согласованных блока. Каждый можно мёрджить и катить отдельно — ничто не блокирует следующее.

```
┌─────────────────────────────────────────────────────────────────┐
│  Блок 1: scan_id (фундамент)                                    │
│  ObserverSettings.current_scan_id (int, монотонно)              │
│  AdSnapshot.last_scan_id (FK-less int, индексируется)           │
│  observer_worker инкрементирует и проставляет в batch save      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Блок 2: ручной rollover суток                                  │
│  POST /api/observer/start-new-cabinet-day                       │
│  scan_guard.py больше не вызывает is_cabinet_day_reset_scan     │
│  snapshot_writer._maybe_rollover_cabinet_day → удалить вызов    │
│  (логика архивации переносится в endpoint)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Блок 3: UI-плитка Observer                                     │
│  GET /api/dashboard/observer-status                             │
│  ObserverStatusTile.jsx рядом с KPI                             │
│  Использует scan_id (блок 1) + worker_status + guard state      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Блок 4: чистка логов                                           │
│  Удалить logger.info("сканирование отключено") из main цикла    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Блок 1 — `scan_id`

### 4.1. Модель данных

**`ObserverSettings`** (`core/models/__init__.py`):
```python
# Монотонный счётчик циклов observer. Инкрементируется в начале каждого цикла,
# где будет реальное сканирование (не sleep, не disabled).
current_scan_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
```

**`AdSnapshot`** (`core/models/__init__.py`):
```python
# Идентификатор последнего scan-цикла, обновившего эту запись.
# Не FK — это просто метка, чтобы dashboard мог отфильтровать «последний батч».
last_scan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
```

### 4.2. Миграция

Alembic-revision `add_scan_id_tracking`:
```python
op.add_column(
    "observer_settings",
    sa.Column("current_scan_id", sa.BigInteger(), nullable=False, server_default="0"),
)
op.add_column(
    "ad_snapshots",
    sa.Column("last_scan_id", sa.BigInteger(), nullable=True),
)
op.create_index("ix_ad_snapshots_last_scan_id", "ad_snapshots", ["last_scan_id"])
```

Backfill не нужен — `NULL` интерпретируется как «снэпшот старее, чем механизм». Дашборд будет считать `current_scan_id` из `observer_settings` и фильтровать `AdSnapshot.last_scan_id == current_scan_id`. До первого нового скана плитка покажет `0/N`, что честно отражает реальность.

### 4.3. Логика observer_worker

В `apps/observer_worker/main.py:_run_scan_cycle` в самом начале (после проверки `is_scanning_enabled` и до парсинга):
```python
# Инкрементируем scan_id один раз на цикл. Атомарно через UPDATE ... RETURNING.
async with session_factory() as session:
    settings = await get_or_create_observer_settings(session)
    settings.current_scan_id += 1
    current_scan_id = settings.current_scan_id
    await session.commit()
```

При батч-сохранении (`snapshot_writer.save_snapshot_batch` или где сейчас формируется `AdSnapshot`) — проставлять `last_scan_id=current_scan_id` всем записям.

Если `ZeroScanGuard.should_skip` вернул `True` — `current_scan_id` уже инкрементирован, но `last_scan_id` нигде не проставится. Это нормально: следующий принятый батч получит новое значение и dashboard будет показывать его.

### 4.4. Использование в dashboard

В `apps/api/routers/dashboard.py` функция, где сейчас считается `state_distribution` с фильтром по `snapshot_cutoff`:
```python
# Было:
# .where(AdSnapshot.last_observed_at >= snapshot_cutoff)

# Стало:
settings = await get_or_create_observer_settings(db)
current_scan_id = settings.current_scan_id
# .where(AdSnapshot.last_scan_id == current_scan_id)
```

Если `current_scan_id == 0` (миграция применена, но ни одного нового скана не было) — показываем пустое распределение и в плитке (блок 3) пишем «ждём первый scan».

---

## 5. Блок 2 — ручной rollover суток кабинета

### 5.1. Что удаляем

В `core/observer/scan_guard.py`:
- Импорт `is_cabinet_day_reset_scan`.
- Ветка `if not is_cabinet_day_reset_scan(snapshot_data):` целиком — guard теперь не различает «zero-scan кабинета» и «случайный пустой батч». Любой пустой/частичный батч идёт через тот же путь подтверждения. Это упрощает класс.

После упрощения `ZeroScanGuard` отвечает ровно за одно: **пропустить подозрительный батч до подтверждения**. Партишн логики «новый день» из него уходит.

В `core/observer/snapshot_writer.py`:
- Удалить вызов `_maybe_rollover_cabinet_day` из основного пути сохранения батча (строка ~615).
- Удалить саму функцию `_maybe_rollover_cabinet_day` и связанный импорт `is_cabinet_day_reset_scan` (если больше нигде не используется).

### 5.2. Что добавляем

**API endpoint** `POST /api/observer/start-new-cabinet-day` в `apps/api/routers/observer.py` (или dashboard.py — где сейчас тогл сканирования). Тело:
```python
async def start_new_cabinet_day(db: AsyncSession = Depends(get_db)) -> dict:
    """Закрывает текущие сутки кабинета и открывает новые.

    Архивирует все живые снэпшоты текущего дня в CabinetDayArchive,
    сдвигает observer_settings.cabinet_day_started_at на now().
    Не трогает scan_id и не блокирует observer — следующий цикл просто
    начнёт писать снэпшоты, относящиеся уже к новому дню.
    """
    settings = await get_or_create_observer_settings(db)
    now = datetime.now(UTC)

    stmt = select(AdSnapshot).options(...)
    if settings.cabinet_day_started_at is not None:
        stmt = stmt.where(AdSnapshot.last_observed_at >= settings.cabinet_day_started_at)
    current_snapshots = (await db.execute(stmt)).scalars().all()

    archived = 0
    if current_snapshots and any(has_any_metric_value(s) for s in current_snapshots):
        summary_json, campaigns_json, ads_json = build_cabinet_day_archive_payload(current_snapshots)
        db.add(CabinetDayArchive(
            started_at=settings.cabinet_day_started_at or now,
            ended_at=now,
            reset_detected_at=now,
            ads_count=len(current_snapshots),
            summary_json=summary_json,
            campaigns_json=campaigns_json,
            ads_json=ads_json,
        ))
        archived = len(current_snapshots)

    settings.cabinet_day_started_at = now
    await db.commit()

    return {"ok": True, "archived_ads": archived, "new_day_started_at": now.isoformat()}
```

### 5.3. UI

Кнопка «Начать новые сутки кабинета» в `DashboardPage.jsx` рядом с тоглом сканирования (логичная группировка — оба про «состояние observer»). По клику:
1. Показать confirm: «Это закроет текущий день кабинета и архивирует N объявлений. Продолжить?»
2. POST на endpoint.
3. После успеха — toast «Сутки переоткрыты, архивировано N объявлений» и refetch dashboard.

### 5.4. Совместимость

Поле `cabinet_day_started_at` остаётся как было, его читают:
- `core/enable_tasks.py` — фильтрует enable-recommendations по границе дня.
- `apps/api/routers/dashboard.py` (несколько мест) — считает «всё после начала суток».

Эти места не трогаем — они работают с `cabinet_day_started_at` как с timestamp, не важно, кто его поставил (раньше — auto-rollover, теперь — кнопка).

---

## 6. Блок 3 — UI-плитка `ObserverStatusTile`

### 6.1. API

`GET /api/dashboard/observer-status` в `apps/api/routers/dashboard.py`:

```python
@router.get("/dashboard/observer-status")
async def get_observer_status(db: AsyncSession = Depends(get_db)) -> dict:
    settings = await get_or_create_observer_settings(db)

    # Размер последнего принятого батча = count(AdSnapshot WHERE last_scan_id == current_scan_id)
    last_batch_size = await db.scalar(
        select(func.count(AdSnapshot.id))
        .where(AdSnapshot.last_scan_id == settings.current_scan_id)
    ) if settings.current_scan_id > 0 else 0

    # Всего объявлений «живых» в текущем дне кабинета (для сравнения «X из Y»)
    active_total_stmt = select(func.count(AdSnapshot.id))
    if settings.cabinet_day_started_at is not None:
        active_total_stmt = active_total_stmt.where(
            AdSnapshot.last_observed_at >= settings.cabinet_day_started_at
        )
    active_total = await db.scalar(active_total_stmt) or 0

    return {
        "is_scanning_enabled": settings.is_scanning_enabled,
        "worker_status": settings.worker_status,         # "scanning" / "sleeping" / "idle" / "error"
        "worker_message": settings.worker_message,
        "worker_heartbeat_at": settings.worker_heartbeat_at,
        "worker_last_error": settings.worker_last_error,
        "current_scan_id": settings.current_scan_id,
        "last_batch_size": last_batch_size,
        "active_total": active_total,
        "next_scan_at": settings.next_scan_at,
        "cabinet_day_started_at": settings.cabinet_day_started_at,
        # Guard-state выставляется observer'ом в worker_message при skip'е.
        # Отдельного поля не вводим — просто парсим worker_status == "guard_pending".
    }
```

### 6.2. Состояния worker_status, которые ставит observer

Расширяем уже существующее поле `ObserverSettings.worker_status`. Сейчас observer его уже как-то заполняет — нужно убедиться, что он покрывает следующие значения:
- `"scanning"` — прямо сейчас идёт цикл (парсит таблицу).
- `"sleeping"` — между циклами (полезно: видно `next_scan_at`).
- `"guard_pending_zero"` — последний батч был пустой, ждём подтверждения.
- `"guard_pending_partial"` — последний батч был частичный, ждём подтверждения.
- `"disabled"` — `is_scanning_enabled=False`.
- `"error"` — последний цикл упал.

Если каких-то нет — добавить установку в `apps/observer_worker/main.py` в соответствующих точках. ZeroScanGuard теперь возвращает не `bool`, а `enum SkipReason | None`, чтобы observer мог проставить корректный статус. Расширение API guard'а минимальное:

```python
class GuardSkipReason(str, Enum):
    ZERO_SCAN_PENDING = "zero_scan_pending"
    PARTIAL_BATCH_PENDING = "partial_batch_pending"

def should_skip(self, snapshot_data: list[dict]) -> GuardSkipReason | None:
    # вместо True/False
```

### 6.3. React-компонент

`frontend/src/components/observer/ObserverStatusTile.jsx`:

```jsx
// Компактная плитка observer-статуса. Полит /api/dashboard/observer-status раз в 5 сек.
// Цвет фона/бейджа зависит от worker_status: scanning=blue, sleeping=gray,
// guard_pending_*=yellow, disabled=muted, error=red.
```

Содержимое плитки (одна строка значений с лейблами над ними, как остальные KPI):

```
Observer   ●Scanning      Цикл #1247    Последний батч 42/42    Sleep до 14:32:05
                                                                 [Начать новые сутки]
```

При `guard_pending_partial`:
```
Observer   ⚠ Guard pending   Цикл #1247    Последний батч 34/42    Ждёт подтверждения
```

Размещение: первая плитка в ряду KPI на `DashboardPage.jsx`. Polling — общий `useAsyncPolling(5000)`.

Кнопка «Начать новые сутки кабинета» — в правом углу плитки или в выпадающем меню (зависит от того, как frontend дизайнер положит, но в дизайне фиксируем: она логически часть этой плитки, а не отдельный блок).

---

## 7. Блок 4 — чистка логов

В `apps/observer_worker/main.py` найти `logger.info("сканирование отключено, пропускаем цикл")` (или подобное) и **удалить полностью**. Состояние видно в UI и в БД (`is_scanning_enabled`), отдельный лог не нужен.

Заодно проверить, нет ли других мест, где в idle-цикле пишется INFO каждые 10 секунд. Если есть — удалить тем же принципом.

---

## 8. План тестирования

### 8.1. Unit-тесты

- `tests/unit/test_scan_guard.py` — обновить: `should_skip` теперь возвращает `GuardSkipReason | None`, ветка с `is_cabinet_day_reset_scan` удалена. Тест-кейсы:
  - Пустой батч на пустом state → возвращает `ZERO_SCAN_PENDING`.
  - Повторный пустой батч → возвращает `None` (принимаем).
  - Резкое сжатие батча (42 → 30) → `PARTIAL_BATCH_PENDING`.
  - Повторное сжатие → `None` (принимаем).
- `tests/unit/test_dashboard_state_distribution.py` (новый) — мокаем `AdSnapshot` с разными `last_scan_id`, проверяем что в распределение попадают только записи с `last_scan_id == current_scan_id`.

### 8.2. Integration

- `tests/integration/test_observer_scan_id.py` (новый) — реальный цикл observer (с мок-парсером):
  - Первый цикл с 42 объявлениями → в БД `current_scan_id=1`, у всех 42 `last_scan_id=1`.
  - Второй цикл с 40 объявлениями (FB вернул меньше, но это не подозрительный partial) → `current_scan_id=2`, у 40 `last_scan_id=2`, у 2 «выпавших» осталось `last_scan_id=1`.
  - Dashboard показывает `40/42`.
- `tests/integration/test_manual_cabinet_day_rollover.py` (новый) — POST на endpoint, проверяем `CabinetDayArchive` создаётся, `cabinet_day_started_at` сдвигается.

### 8.3. Smoke вручную

1. Запустить `./run.sh`, открыть dashboard.
2. Убедиться, что плитка Observer показывает «Sleeping, следующий скан через X сек».
3. Дождаться скана → плитка переключается в «Scanning», потом «Sleeping, последний батч N/N».
4. Нажать «Начать новые сутки» → confirm → toast «архивировано N».
5. Проверить в БД: `cabinet_day_started_at` обновлён, в `cabinet_day_archives` новая запись.
6. Проверить `tail -f .logs/observer.log` — никаких повторов «сканирование отключено».

---

## 9. Порядок выкатки

1. **Блок 1** (scan_id) — миграция + observer + dashboard. Без него остальное не имеет смысла.
2. **Блок 4** (чистка логов) — один файл, можно катить параллельно с (1).
3. **Блок 2** (ручной rollover) — после (1), потому что endpoint должен жить в стабильной кодовой базе. Удаление auto-rollover из guard — отдельным коммитом, чтобы можно было откатить.
4. **Блок 3** (UI-плитка) — после (1) и (2), потому что плитке нужны и `scan_id`, и кнопка rollover.

Между блоками — `make verify` (lint + frontend build + smoke).

---

## 10. Риски

| Риск | Митигация |
|---|---|
| После удаления auto-rollover пользователь забудет нажать кнопку и день будет тянуться неделями | В плитке Observer показывать «День кабинета: открыт N часов назад» — если > 30 часов, подсветить жёлтым. |
| `current_scan_id += 1` на каждом цикле может конфликтовать при двух observer-ах одновременно | observer запускается в одном экземпляре (документировано в архитектуре). Если когда-то будет несколько — переделать на `nextval` sequence. |
| `last_scan_id` на старых снэпшотах = NULL → дашборд покажет пустоту до первого нового скана | Это ожидаемо и честно. Альтернатива (backfill) — больше шума, чем пользы, т.к. через 1-2 минуты после деплоя всё равно будет первый новый скан. |
| Удаление `_maybe_rollover_cabinet_day` сломает то, что на неё косвенно полагается | Перед удалением — `grep -rn "_maybe_rollover_cabinet_day\|is_cabinet_day_reset_scan"` и убедиться, что больше нигде не вызывается. |

---

## 11. Что НЕ делаем (явно)

- Не вводим WebSocket для realtime observer-статуса. Polling 5s достаточно.
- Не делаем «историю последних 10 циклов» в плитке. Если будет нужно — отдельная страница.
- Не меняем формат `worker_message`. Используем как есть.
- Не трогаем enable_recommendation_worker, telegram_poller, disable/enable workers.
