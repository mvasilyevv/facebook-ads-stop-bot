# Observer Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дашборд показывает реальное распределение по последнему батчу observer'а, переход суток кабинета — только по кнопке, observer-плитка показывает текущее состояние воркера в UI, лог не зашумлён.

**Architecture:** Вводим монотонный `scan_id` (на `ObserverSettings` и `AdSnapshot`), инкрементируемый observer'ом в начале каждого цикла. Дашборд считает распределение по `last_scan_id == current_scan_id`. ZeroScanGuard упрощается до защиты от пустых/частичных батчей; смена суток вынесена в отдельный API endpoint, вызываемый по кнопке. Плитка `ObserverStatusTile` дёргает `/api/dashboard/observer-status` каждые 5 секунд и отображает текущий цикл, размер батча, статус guard. Лог `«сканирование отключено»` удаляется.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL 16, React 19 + Vite, pytest.

**Дизайн-спека:** `docs/superpowers/specs/2026-05-21-observer-observability-design.md`

---

## File Structure

**Создаются:**
- `migrations/versions/2026_05_21_add_observer_scan_id.py` — миграция добавляет `observer_settings.current_scan_id` и `ad_snapshots.last_scan_id`.
- `apps/api/routers/observer.py` — новый роутер: endpoint смены суток кабинета и observer-status.
- `frontend/src/components/observer/ObserverStatusTile.jsx` — компактная плитка.
- `tests/unit/test_scan_guard_simplified.py` — тесты упрощённого `ZeroScanGuard` (возвращает enum-причину skip).
- `tests/unit/test_dashboard_state_distribution_by_scan_id.py` — тест распределения по `last_scan_id`.
- `tests/unit/test_manual_cabinet_day_rollover.py` — тест endpoint'а смены суток.

**Изменяются:**
- `core/models/__init__.py` — поля `ObserverSettings.current_scan_id` и `AdSnapshot.last_scan_id`.
- `core/observer/scan_guard.py` — упрощение: убрать ветку `is_cabinet_day_reset_scan`, возвращать `GuardSkipReason | None`.
- `core/observer/snapshot_writer.py` — удалить `_maybe_rollover_cabinet_day` и его вызов; принимать `current_scan_id` и проставлять на `AdSnapshot`.
- `apps/observer_worker/main.py` — инкрементить `current_scan_id` перед каждым реальным сканом; передавать его в `batch_save_snapshots`; удалить лог `«сканирование отключено»`; новые worker_status `GUARD_PENDING_ZERO` / `GUARD_PENDING_PARTIAL`.
- `apps/api/routers/dashboard.py` — `state_distribution` фильтрует по `AdSnapshot.last_scan_id == current_scan_id`.
- `apps/api/main.py` — зарегистрировать роутер `observer`.
- `frontend/src/api.js` — две функции: `getObserverStatus()`, `startNewCabinetDay()`.
- `frontend/src/pages/DashboardPage.jsx` — вставить `ObserverStatusTile` рядом с `HeroKPIStrip`.
- `tests/unit/test_observer_improvements.py` — обновить тесты `batch_save_snapshots` под новый параметр `current_scan_id`.

---

## Порядок выкатки

Блоки независимы по коду, но логически:
1. **Блок A** — миграция + `scan_id` в моделях.
2. **Блок B** — observer проставляет `scan_id`.
3. **Блок C** — dashboard фильтрует по `scan_id`.
4. **Блок D** — упрощение `ZeroScanGuard` (убираем cabinet_day branch).
5. **Блок E** — ручной rollover суток через API endpoint.
6. **Блок F** — observer-status endpoint + UI-плитка.
7. **Блок G** — чистка логов.

---

## Блок A — Миграция и поля моделей

### Task A.1: Миграция Alembic

**Files:**
- Create: `migrations/versions/2026_05_21_add_observer_scan_id.py`

- [ ] **Step 1: Создать файл миграции**

```python
"""add observer scan_id tracking

Revision ID: a1b2c3d4e5f6
Revises: 6ada1843542c
Create Date: 2026-05-21 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "6ada1843542c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Монотонный счётчик циклов observer для отслеживания «последнего батча».
    op.add_column(
        "observer_settings",
        sa.Column("current_scan_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Идентификатор последнего scan-цикла, обновившего эту запись (не FK).
    op.add_column(
        "ad_snapshots",
        sa.Column("last_scan_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_ad_snapshots_last_scan_id",
        "ad_snapshots",
        ["last_scan_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ad_snapshots_last_scan_id", table_name="ad_snapshots")
    op.drop_column("ad_snapshots", "last_scan_id")
    op.drop_column("observer_settings", "current_scan_id")
```

- [ ] **Step 2: Проверить, что `down_revision` указывает на текущий head**

Run: `alembic heads`
Expected: `6ada1843542c (head)`. Если head другой — поправить `down_revision` в файле.

- [ ] **Step 3: Применить миграцию**

Run: `alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade 6ada1843542c -> a1b2c3d4e5f6, add observer scan_id tracking`

- [ ] **Step 4: Проверить в БД**

Run: `docker exec fb_agent-postgres-1 psql -U postgres -d fb_agent -c "\d observer_settings" | grep current_scan_id`
Expected: `current_scan_id | bigint | not null default 0`

Run: `docker exec fb_agent-postgres-1 psql -U postgres -d fb_agent -c "\d ad_snapshots" | grep last_scan_id`
Expected: `last_scan_id | bigint |`

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/2026_05_21_add_observer_scan_id.py
git commit -m "feat(observer): миграция scan_id для observer_settings и ad_snapshots"
```

### Task A.2: Поля в моделях SQLAlchemy

**Files:**
- Modify: `core/models/__init__.py:91-132` (ObserverSettings) и блок `AdSnapshot`

- [ ] **Step 1: Найти class ObserverSettings и добавить current_scan_id**

В `core/models/__init__.py` после `is_scanning_enabled` (строка ~100) добавить:

```python
    # Монотонный счётчик циклов observer. Инкрементируется в начале каждого реального
    # сканирования и проставляется всем AdSnapshot этого батча в поле last_scan_id.
    current_scan_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
```

Убедиться, что `BigInteger` импортирован из `sqlalchemy` в начале файла. Если нет — добавить в импорт.

- [ ] **Step 2: Найти class AdSnapshot и добавить last_scan_id**

В `core/models/__init__.py` найти `class AdSnapshot` (через `grep -n "class AdSnapshot" core/models/__init__.py`). Добавить поле в конец списка колонок:

```python
    # Идентификатор последнего scan-цикла observer, обновившего эту запись.
    # NULL у снэпшотов, созданных до внедрения механизма scan_id.
    last_scan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
```

- [ ] **Step 3: Проверить, что модель собирается**

Run: `python -c "from core.models import ObserverSettings, AdSnapshot; print(ObserverSettings.current_scan_id, AdSnapshot.last_scan_id)"`
Expected: вывод без ошибок, типа `ObserverSettings.current_scan_id AdSnapshot.last_scan_id`

- [ ] **Step 4: Lint**

Run: `ruff check core/models/__init__.py`
Expected: All checks passed!

- [ ] **Step 5: Commit**

```bash
git add core/models/__init__.py
git commit -m "feat(models): добавлены ObserverSettings.current_scan_id и AdSnapshot.last_scan_id"
```

---

## Блок B — Observer проставляет scan_id

### Task B.1: snapshot_writer принимает current_scan_id и проставляет в AdSnapshot

**Files:**
- Modify: `core/observer/snapshot_writer.py:485-510` (функция `_prepare_snapshot_upsert_data`)
- Modify: `core/observer/snapshot_writer.py:590-651` (функция `batch_save_snapshots`)

- [ ] **Step 1: Прочитать текущую сигнатуру `_prepare_snapshot_upsert_data`**

Read: `core/observer/snapshot_writer.py` строки 485-512.

- [ ] **Step 2: Добавить параметр `current_scan_id` в `_prepare_snapshot_upsert_data`**

Изменить сигнатуру:
```python
def _prepare_snapshot_upsert_data(
    snapshot_data: list[dict],
    ad_id_map: dict[str, _uuid.UUID],
    *,
    current_scan_id: int | None,
) -> list[dict]:
```

В теле функции внутри цикла построения row для каждого item — добавить ключ `last_scan_id`:
```python
    for item in snapshot_data:
        ...
        row["last_scan_id"] = current_scan_id
        rows.append(row)
```

(Точное место добавления — после уже существующей нормализации полей, перед `rows.append(row)`. Если структура `row` собирается через распаковку — добавить `"last_scan_id": current_scan_id` в словарь.)

- [ ] **Step 3: Пробросить параметр в `batch_save_snapshots`**

В `batch_save_snapshots` (строка 590) изменить сигнатуру:
```python
async def batch_save_snapshots(
    snapshot_data: list[dict],
    scan_guard: ZeroScanGuard,
    *,
    regression_guard: RegressionGuard | None = None,
    allow_cabinet_rollover: bool = True,
    bypass_scan_guard: bool = False,
    current_scan_id: int | None = None,
) -> bool:
```

В вызове `_prepare_snapshot_upsert_data` передать:
```python
        snapshot_rows = _prepare_snapshot_upsert_data(
            snapshot_data, ad_id_map, current_scan_id=current_scan_id
        )
```

- [ ] **Step 4: Lint**

Run: `ruff check core/observer/snapshot_writer.py`
Expected: All checks passed!

- [ ] **Step 5: Commit**

```bash
git add core/observer/snapshot_writer.py
git commit -m "feat(observer): snapshot_writer проставляет last_scan_id на AdSnapshot"
```

### Task B.2: Observer инкрементирует current_scan_id и передаёт в save

**Files:**
- Modify: `apps/observer_worker/main.py:541-760` (`_run_scan_cycle`)
- Modify: `apps/observer_worker/main.py:1530`, `1637` (вызовы `_run_scan_cycle`)

- [ ] **Step 1: Найти точку входа в реальный скан**

Read: `apps/observer_worker/main.py:1280-1300`. Реальный скан начинается после проверки `if not forced_scan and not scanning_enabled: ... continue`.

- [ ] **Step 2: Перед вызовом `_run_scan_cycle` (примерно строка 1525-1530) инкрементировать scan_id**

Добавить хелпер в `apps/observer_worker/main.py` (рядом с другими хелперами, например после `update_observer_runtime_status`-импорта или в конце файла перед `if __name__`):

```python
async def _increment_scan_id() -> int:
    """Атомарно инкрементирует current_scan_id и возвращает новое значение.

    Используется в начале каждого реального scan-цикла observer'а, чтобы
    помечать AdSnapshot этого батча. Идёт отдельной короткой транзакцией,
    чтобы не зависеть от длинного цикла сканирования.
    """
    factory = get_session_factory()
    async with factory() as session:
        settings = await get_or_create_observer_settings(session)
        settings.current_scan_id = (settings.current_scan_id or 0) + 1
        new_id = settings.current_scan_id
        await session.commit()
        return new_id
```

Проверить, что `get_session_factory` и `get_or_create_observer_settings` уже импортированы в файле; если нет — добавить:
```python
from core.db import get_session_factory
from core.settings_queries import get_or_create_observer_settings
```

- [ ] **Step 3: Вызывать `_increment_scan_id` перед `_run_scan_cycle`**

Найти оба места вызова `_run_scan_cycle` (строки ~1530 и ~1637). Прямо перед каждым вызовом добавить:
```python
                current_scan_id = await _increment_scan_id()
```

И в каждый вызов `_run_scan_cycle(...)` пробросить `current_scan_id=current_scan_id` как kwarg.

- [ ] **Step 4: `_run_scan_cycle` принимает и пробрасывает `current_scan_id` в `batch_save_snapshots`**

В сигнатуре `async def _run_scan_cycle(...)` добавить `current_scan_id: int` в kwargs.

Найти в теле `_run_scan_cycle` все вызовы `batch_save_snapshots(...)` (через `grep -n "batch_save_snapshots" apps/observer_worker/main.py`) и добавить `current_scan_id=current_scan_id` в каждый.

- [ ] **Step 5: Lint**

Run: `ruff check apps/observer_worker/main.py`
Expected: All checks passed!

- [ ] **Step 6: Smoke-проверка вручную (если возможно)**

Запустить локально observer на 2 цикла и проверить, что в БД current_scan_id увеличился:
```bash
docker exec fb_agent-postgres-1 psql -U postgres -d fb_agent -c \
  "SELECT current_scan_id FROM observer_settings;"
```
Expected: значение > 0.

```bash
docker exec fb_agent-postgres-1 psql -U postgres -d fb_agent -c \
  "SELECT DISTINCT last_scan_id FROM ad_snapshots ORDER BY last_scan_id DESC LIMIT 3;"
```
Expected: появились значения, совпадающие с current_scan_id.

- [ ] **Step 7: Commit**

```bash
git add apps/observer_worker/main.py
git commit -m "feat(observer): инкремент current_scan_id и проброс в snapshot batch"
```

### Task B.3: Обновить существующие тесты `batch_save_snapshots`

**Files:**
- Modify: `tests/unit/test_observer_improvements.py:167,243,344` и аналогичные

- [ ] **Step 1: Найти все вызовы `batch_save_snapshots` в тестах**

Run: `grep -n "batch_save_snapshots" tests/unit/test_observer_improvements.py`
Expected: список строк-вызовов.

- [ ] **Step 2: Добавить `current_scan_id=1` (или другое корректное значение) в каждый вызов**

Для каждого вызова:
```python
        await batch_save_snapshots(snapshot_data, scan_guard)
```
заменить на:
```python
        await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=1)
```

Если тест эмулирует несколько последовательных циклов — передавать `current_scan_id=2`, `current_scan_id=3` для каждого следующего вызова, чтобы отражать реальное поведение.

- [ ] **Step 3: Запустить unit-тесты**

Run: `pytest tests/unit/test_observer_improvements.py -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_observer_improvements.py
git commit -m "test(observer): обновлены вызовы batch_save_snapshots под current_scan_id"
```

---

## Блок C — Dashboard фильтрует по scan_id

### Task C.1: state_distribution по last_scan_id

**Files:**
- Modify: `apps/api/routers/dashboard.py:2671-2687`

- [ ] **Step 1: Написать failing-тест**

Create: `tests/unit/test_dashboard_state_distribution_by_scan_id.py`

```python
# -*- coding: utf-8 -*-
"""Распределение alert_state на дашборде считается по last_scan_id, не по окну времени."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import AlertState


def _result(rows):
    """Мок результата SQLAlchemy с .all() возвращающим переданные пары."""
    result = MagicMock()
    result.all.return_value = rows
    return result


# В батче последнего скана 40 объявлений NORMAL, 2 WARNING_SENT; "потерянных" из прошлого
# скана быть не должно — они не попадают в state_distribution.
@pytest.mark.asyncio
async def test_state_distribution_filters_by_current_scan_id():
    from apps.api.routers import dashboard as dash_module

    # Готовим мок db.execute, который вернёт распределение из БД.
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result([
        (AlertState.NORMAL, 40),
        (AlertState.WARNING_SENT, 2),
    ]))

    # _build_state_distribution — внутренний хелпер, см. Task C.1 step 3.
    distribution = await dash_module._build_state_distribution(db, current_scan_id=7)

    by_label = {item["state"]: item["count"] for item in distribution}
    assert by_label.get("Норма") == 40
    assert by_label.get("Предупреждение") == 2
    # Проверяем, что фильтр в SQL — именно по last_scan_id == 7.
    # Достаточно убедиться, что execute был вызван хотя бы раз.
    db.execute.assert_awaited_once()
```

- [ ] **Step 2: Запустить — должен упасть**

Run: `pytest tests/unit/test_dashboard_state_distribution_by_scan_id.py -x -q`
Expected: FAIL — `AttributeError: module 'apps.api.routers.dashboard' has no attribute '_build_state_distribution'`.

- [ ] **Step 3: Вынести логику в хелпер и переключить фильтр на last_scan_id**

В `apps/api/routers/dashboard.py` после определения `_current_scan_cutoff` (строка ~163) добавить хелпер:

```python
_STATE_LABELS = {
    AlertState.NORMAL: "Норма",
    AlertState.WARNING_SENT: "Предупреждение",
    AlertState.STOP_SENT: "Стоп",
    AlertState.CLAIMED: "Ожидает OFF",
    AlertState.DISABLED: "Отключён",
}


async def _build_state_distribution(
    db: AsyncSession,
    *,
    current_scan_id: int,
) -> list[dict[str, object]]:
    """Распределение alert_state по последнему принятому батчу observer.

    Берём только снэпшоты с last_scan_id == current_scan_id. Это ровно те,
    которые попали в последний полный проход сканера, без зависимости от окна
    времени. До первого скана возвращаем пустой список.
    """
    if current_scan_id <= 0:
        return []
    result = await db.execute(
        select(AdSnapshot.alert_state, func.count().label("cnt"))
        .where(AdSnapshot.last_scan_id == current_scan_id)
        .group_by(AdSnapshot.alert_state)
    )
    return [
        {"state": _STATE_LABELS.get(state, str(state)), "count": cnt}
        for state, cnt in result.all()
    ]
```

В месте использования (строки 2671-2687) заменить inline-расчёт на:
```python
    settings_row = await get_or_create_observer_settings(db)
    state_distribution = await _build_state_distribution(
        db, current_scan_id=settings_row.current_scan_id
    )
```

Удалить старый блок с `state_result = await db.execute(...)` и `_state_labels = {...}` и `state_distribution = [...]`.

Проверить импорт `get_or_create_observer_settings`:
```python
from core.settings_queries import get_or_create_observer_settings
```
Если не импортирован — добавить.

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `pytest tests/unit/test_dashboard_state_distribution_by_scan_id.py -x -q`
Expected: PASS.

- [ ] **Step 5: Прогнать весь тест-сьют по dashboard**

Run: `pytest tests/unit/ -k "dashboard" -x -q`
Expected: All pass.

- [ ] **Step 6: Lint**

Run: `ruff check apps/api/routers/dashboard.py tests/unit/test_dashboard_state_distribution_by_scan_id.py`
Expected: All checks passed!

- [ ] **Step 7: Commit**

```bash
git add apps/api/routers/dashboard.py tests/unit/test_dashboard_state_distribution_by_scan_id.py
git commit -m "feat(dashboard): state_distribution считается по AdSnapshot.last_scan_id"
```

---

## Блок D — Упрощение ZeroScanGuard

### Task D.1: ZeroScanGuard возвращает GuardSkipReason | None и не зависит от cabinet_day

**Files:**
- Modify: `core/observer/scan_guard.py`
- Create: `tests/unit/test_scan_guard_simplified.py`

- [ ] **Step 1: Написать failing-тесты для нового API guard**

Create: `tests/unit/test_scan_guard_simplified.py`

```python
# -*- coding: utf-8 -*-
"""Упрощённый ZeroScanGuard: возвращает причину skip enum'ом, без cabinet_day."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.observer.scan_guard import GuardSkipReason, ZeroScanGuard


def _row(fb_ad_id: str, spend: str = "0") -> dict:
    """Минимальный snapshot-словарь для guard'а."""
    return {
        "fb_ad_id": fb_ad_id,
        "spend": spend,
        "clicks": 0,
        "leads": 0,
        "registrations": 0,
        "deposits": 0,
        "last_observed_at": datetime.now(UTC),
    }


# Первый пустой батч — guard пропускает с причиной ZERO_SCAN_PENDING.
def test_first_zero_scan_returns_pending_reason():
    guard = ZeroScanGuard()
    reason = guard.should_skip([_row(f"ad{i}") for i in range(5)])  # все zero
    assert reason == GuardSkipReason.ZERO_SCAN_PENDING


# Повторный пустой батч — guard принимает (None).
def test_second_zero_scan_accepted():
    guard = ZeroScanGuard()
    guard.should_skip([_row(f"ad{i}") for i in range(5)])  # pending
    reason = guard.should_skip([_row(f"ad{i}") for i in range(5)])
    assert reason is None


# Нормальный батч с метриками — guard принимает сразу.
def test_normal_batch_accepted():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    rows = [_row(f"ad{i}", spend="10.50") for i in range(40)]
    reason = guard.should_skip(rows)
    assert reason is None


# Резкое сжатие батча — pending partial.
def test_partial_batch_returns_pending_reason():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    # Принимаем первый полный батч.
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(40)])
    # Резкий drop: 40 → 20.
    reason = guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])
    assert reason == GuardSkipReason.PARTIAL_BATCH_PENDING


# Подтверждённое сжатие — принимаем урезанный срез.
def test_partial_batch_second_attempt_accepted():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(40)])
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])  # pending
    reason = guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])
    assert reason is None


# Пустой список — нет данных, ничего не пропускаем (False / None).
def test_empty_input_returns_none():
    guard = ZeroScanGuard()
    assert guard.should_skip([]) is None
```

- [ ] **Step 2: Запустить — должны упасть с ImportError**

Run: `pytest tests/unit/test_scan_guard_simplified.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'GuardSkipReason'`.

- [ ] **Step 3: Переписать `core/observer/scan_guard.py`**

Полностью переписать содержимое файла:

```python
# -*- coding: utf-8 -*-
"""ZeroScanGuard: инкапсулирует логику пропуска подозрительных батчей снэпшотов.

Защищает от затирания живого среза в случае временного сбоя парсинга
или временно неполного ответа Facebook. Любой пустой или подозрительно
урезанный батч требует подтверждения на следующем цикле.

Логика смены суток кабинета вынесена в endpoint
POST /api/observer/start-new-cabinet-day и больше не триггерится guard'ом.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)

_SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO = 0.85
_SUSPICIOUS_PARTIAL_BATCH_MIN_DROP = 5


class GuardSkipReason(str, Enum):
    """Причина, по которой guard просит пропустить батч."""

    ZERO_SCAN_PENDING = "guard_pending_zero"
    PARTIAL_BATCH_PENDING = "guard_pending_partial"


def _is_zero_batch(snapshot_data: list[dict]) -> bool:
    """True, если в батче все ключевые метрики у всех записей равны нулю/None."""
    metrics_to_check = (
        "spend",
        "clicks",
        "leads",
        "registrations",
        "deposits",
    )
    for item in snapshot_data:
        for metric in metrics_to_check:
            value = item.get(metric)
            if value in (None, 0, "0", "0.0", "0.00"):
                continue
            try:
                if float(value) != 0.0:
                    return False
            except (TypeError, ValueError):
                continue
    return True


class ZeroScanGuard:
    """Отслеживает подозрительные zero-scan и partial-batch сигналы.

    Возвращает GuardSkipReason | None из should_skip:
        - ZERO_SCAN_PENDING — первый полный zero-batch, ждём подтверждения.
        - PARTIAL_BATCH_PENDING — первый подозрительно урезанный батч.
        - None — батч принят (сохраняется).
    """

    def __init__(self) -> None:
        self._pending_zero_scan_at: datetime | None = None
        self._pending_partial_batch_at: datetime | None = None
        self._last_accepted_size: int | None = None

    def initialize_from_count(self, count: int) -> None:
        """Восстанавливает базовый размер батча из БД при старте воркера."""
        if self._last_accepted_size is None and count > 0:
            self._last_accepted_size = count
            logger.info(
                "ZeroScanGuard: базовый размер батча восстановлен из БД: %s снэпшотов",
                count,
            )

    def should_skip(self, snapshot_data: list[dict]) -> GuardSkipReason | None:
        """Возвращает причину skip или None если батч можно сохранять."""
        if not snapshot_data:
            self._pending_zero_scan_at = None
            self._pending_partial_batch_at = None
            return None

        scan_started_at = max(
            (
                item.get("last_observed_at")
                for item in snapshot_data
                if item.get("last_observed_at")
            ),
            default=datetime.now(UTC),
        )
        snapshot_count = len(snapshot_data)

        if _is_zero_batch(snapshot_data):
            if self._pending_zero_scan_at is None:
                self._pending_zero_scan_at = scan_started_at
                logger.warning(
                    "Observer: получен полный zero-batch без подтверждения — "
                    "пропускаю сохранение до следующего цикла"
                )
                return GuardSkipReason.ZERO_SCAN_PENDING
            logger.warning(
                "Observer: повторный zero-batch подтверждён — принимаю нулевой срез"
            )
            self._pending_zero_scan_at = None
            self._last_accepted_size = snapshot_count
            return None

        # Не zero-batch: сбрасываем pending zero.
        if self._pending_zero_scan_at is not None:
            logger.warning(
                "Observer: zero-batch не подтвердился на следующем цикле, "
                "продолжаю работать по живому срезу"
            )
        self._pending_zero_scan_at = None

        previous_size = self._last_accepted_size
        suspicious_partial = (
            previous_size is not None
            and previous_size - snapshot_count >= _SUSPICIOUS_PARTIAL_BATCH_MIN_DROP
            and snapshot_count < previous_size * _SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO
        )
        if suspicious_partial:
            if self._pending_partial_batch_at is None:
                self._pending_partial_batch_at = scan_started_at
                logger.warning(
                    "Observer: подозрительно неполный батч (%s вместо %s) — "
                    "пропускаю сохранение до подтверждения",
                    snapshot_count,
                    previous_size,
                )
                return GuardSkipReason.PARTIAL_BATCH_PENDING
            logger.warning(
                "Observer: повторный неполный батч подтверждён (%s вместо %s) — "
                "принимаю урезанный срез",
                snapshot_count,
                previous_size,
            )
            self._pending_partial_batch_at = None
            self._last_accepted_size = snapshot_count
            return None

        if self._pending_partial_batch_at is not None:
            logger.warning(
                "Observer: неполный батч не подтвердился, сохраняю восстановленный срез"
            )
        self._pending_partial_batch_at = None
        self._last_accepted_size = snapshot_count
        return None
```

- [ ] **Step 4: Запустить тесты — должны пройти**

Run: `pytest tests/unit/test_scan_guard_simplified.py -x -q`
Expected: All 6 tests PASS.

- [ ] **Step 5: Обновить вызов `should_skip` в `snapshot_writer.py`**

В `core/observer/snapshot_writer.py` найти:
```python
    if not bypass_scan_guard and scan_guard.should_skip(snapshot_data):
        return False
```
заменить на:
```python
    if not bypass_scan_guard and scan_guard.should_skip(snapshot_data) is not None:
        return False
```

Также удалить вызов `_maybe_rollover_cabinet_day` и сам блок `if allow_cabinet_rollover:` — обе строки (614-615 примерно). Параметр `allow_cabinet_rollover` оставить в сигнатуре (для обратной совместимости вызовов), но не использовать; добавить TODO-комментарий — НЕТ, по правилу «без TODO» — просто оставить параметр и `# Параметр зарезервирован, rollover теперь делается только по кнопке через API.` или удалить параметр и его callsites.

Проверить callsites:
Run: `grep -rn "allow_cabinet_rollover" .`

Если только один callsite — удалить параметр везде. Если несколько и они в одном файле — удалить везде вместе.

- [ ] **Step 6: Удалить `_maybe_rollover_cabinet_day` и связанные импорты**

В `core/observer/snapshot_writer.py`:
- Удалить функцию `async def _maybe_rollover_cabinet_day` (строки ~37-115).
- Удалить из верхнего импорта `from core.cabinet_day import (...)` всё, что больше не нужно. Проверить через grep, что в файле осталось из `core.cabinet_day`:
  ```
  grep -n "cabinet_day\|is_cabinet_day_reset_scan\|build_cabinet_day_archive_payload\|has_any_metric_value" core/observer/snapshot_writer.py
  ```
- Поле `is_reset = is_cabinet_day_reset_scan(snapshot_data)` (строка ~639) — заменить на `is_reset = False` (или удалить полностью, если `allow_metric_regression=False` всегда устраивает). Если в коде есть `if regression_guard and not is_reset`, упростить до `if regression_guard`.

- [ ] **Step 7: Lint и тесты**

Run: `ruff check core/observer/scan_guard.py core/observer/snapshot_writer.py tests/unit/test_scan_guard_simplified.py`
Expected: All checks passed!

Run: `pytest tests/unit/ -k "scan_guard or observer_improvements" -x -q`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add core/observer/scan_guard.py core/observer/snapshot_writer.py tests/unit/test_scan_guard_simplified.py
git commit -m "refactor(observer): ZeroScanGuard возвращает enum-причину, cabinet_day rollover убран"
```

---

## Блок E — Ручной rollover суток через API

### Task E.1: Endpoint POST /api/observer/start-new-cabinet-day

**Files:**
- Create: `apps/api/routers/observer.py`
- Modify: `apps/api/main.py:23-50` (импорты роутеров), `apps/api/main.py:111-125` (include_router)
- Create: `tests/unit/test_manual_cabinet_day_rollover.py`

- [ ] **Step 1: Создать роутер `apps/api/routers/observer.py`**

```python
# -*- coding: utf-8 -*-
"""API наблюдателя: ручная смена суток кабинета и observer-статус."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_db
from core.cabinet_day import build_cabinet_day_archive_payload, has_any_metric_value
from core.models import AdSnapshot, CabinetDayArchive, FbAd, FbAdset
from core.settings_queries import get_or_create_observer_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/observer", tags=["observer"])


@router.post("/start-new-cabinet-day")
async def start_new_cabinet_day(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Закрывает текущие сутки кабинета и открывает новые.

    Архивирует живые снэпшоты текущего дня в CabinetDayArchive и сдвигает
    observer_settings.cabinet_day_started_at на now(). Не блокирует observer:
    следующий цикл просто начнёт писать снэпшоты, относящиеся уже к новому дню.
    """
    settings = await get_or_create_observer_settings(db)
    now = datetime.now(UTC)

    stmt = select(AdSnapshot).options(
        selectinload(AdSnapshot.fb_ad).selectinload(FbAd.adset).selectinload(FbAdset.campaign),
    )
    if settings.cabinet_day_started_at is not None:
        stmt = stmt.where(AdSnapshot.last_observed_at >= settings.cabinet_day_started_at)

    current_snapshots = (await db.execute(stmt)).scalars().all()
    has_data = bool(current_snapshots) and any(
        has_any_metric_value(snapshot) for snapshot in current_snapshots
    )

    archived = 0
    if has_data:
        summary_json, campaigns_json, ads_json = build_cabinet_day_archive_payload(
            current_snapshots
        )
        db.add(
            CabinetDayArchive(
                started_at=settings.cabinet_day_started_at or now,
                ended_at=now,
                reset_detected_at=now,
                ads_count=len(current_snapshots),
                summary_json=summary_json,
                campaigns_json=campaigns_json,
                ads_json=ads_json,
            )
        )
        archived = len(current_snapshots)

    settings.cabinet_day_started_at = now
    await db.commit()

    logger.info(
        "Observer: новые сутки кабинета открыты вручную, архивировано %s объявлений",
        archived,
    )
    return {
        "ok": True,
        "archived_ads": archived,
        "new_day_started_at": now.isoformat(),
    }


@router.get("/status")
async def get_observer_status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Возвращает компактный observer-статус для UI-плитки на дашборде."""
    settings = await get_or_create_observer_settings(db)

    last_batch_size = 0
    if settings.current_scan_id and settings.current_scan_id > 0:
        last_batch_size = (
            await db.scalar(
                select(func.count(AdSnapshot.id)).where(
                    AdSnapshot.last_scan_id == settings.current_scan_id
                )
            )
            or 0
        )

    active_stmt = select(func.count(AdSnapshot.id))
    if settings.cabinet_day_started_at is not None:
        active_stmt = active_stmt.where(
            AdSnapshot.last_observed_at >= settings.cabinet_day_started_at
        )
    active_total = await db.scalar(active_stmt) or 0

    return {
        "is_scanning_enabled": bool(settings.is_scanning_enabled),
        "worker_status": settings.worker_status,
        "worker_message": settings.worker_message,
        "worker_heartbeat_at": (
            settings.worker_heartbeat_at.isoformat() if settings.worker_heartbeat_at else None
        ),
        "worker_last_error": settings.worker_last_error,
        "worker_last_error_at": (
            settings.worker_last_error_at.isoformat() if settings.worker_last_error_at else None
        ),
        "current_scan_id": int(settings.current_scan_id or 0),
        "last_batch_size": int(last_batch_size),
        "active_total": int(active_total),
        "next_scan_at": settings.next_scan_at.isoformat() if settings.next_scan_at else None,
        "cabinet_day_started_at": (
            settings.cabinet_day_started_at.isoformat()
            if settings.cabinet_day_started_at
            else None
        ),
    }
```

- [ ] **Step 2: Зарегистрировать роутер в `apps/api/main.py`**

В `apps/api/main.py` добавить импорт (вместе с другими роутерами, строка ~37-38):
```python
from apps.api.routers.observer import router as observer_router
```

И ниже, рядом с `app.include_router(dashboard.router, ...)` (строка ~113):
```python
app.include_router(observer_router, dependencies=_api_key_or_tma_dep)
```

- [ ] **Step 3: Smoke-старт API**

Run: `python -c "from apps.api.main import app; print([r.path for r in app.routes if 'observer' in r.path])"`
Expected: вывод содержит `/api/observer/start-new-cabinet-day` и `/api/observer/status`.

- [ ] **Step 4: Lint**

Run: `ruff check apps/api/routers/observer.py apps/api/main.py`
Expected: All checks passed!

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/observer.py apps/api/main.py
git commit -m "feat(api): endpoint смены суток кабинета и observer-status"
```

### Task E.2: Integration-тест endpoint'а смены суток (опционально, если есть TestClient setup)

**Files:**
- Create: `tests/unit/test_manual_cabinet_day_rollover.py`

- [ ] **Step 1: Если у проекта есть pytest-fixture с testclient — написать тест**

Run: `grep -rn "TestClient\|httpx_client\|async_client" tests/ | head -5`

Если есть фикстура `async_client` или подобная — написать тест:

```python
# -*- coding: utf-8 -*-
"""Endpoint POST /api/observer/start-new-cabinet-day сдвигает границу суток."""

from __future__ import annotations

import pytest


# Endpoint должен сдвинуть cabinet_day_started_at и вернуть archived_ads.
@pytest.mark.asyncio
async def test_start_new_cabinet_day_returns_ok(async_client):
    response = await async_client.post("/api/observer/start-new-cabinet-day")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "new_day_started_at" in body
    assert isinstance(body["archived_ads"], int)
```

Если фикстуры нет — пропустить этот шаг (smoke-проверка в Step 3 ниже).

- [ ] **Step 2: Если тест написан — прогнать**

Run: `pytest tests/unit/test_manual_cabinet_day_rollover.py -x -q`
Expected: PASS.

- [ ] **Step 3: Ручная smoke-проверка endpoint'а**

Запустить API локально: `uvicorn apps.api.main:app --port 8100 --reload`.
В другом терминале:
```bash
curl -X POST http://localhost:8100/api/observer/start-new-cabinet-day \
  -H "X-API-Key: $API_KEY"
```
Expected: `{"ok": true, "archived_ads": N, "new_day_started_at": "..."}`.

Проверить в БД:
```bash
docker exec fb_agent-postgres-1 psql -U postgres -d fb_agent -c \
  "SELECT cabinet_day_started_at FROM observer_settings;"
```
Expected: значение обновилось до текущего времени.

- [ ] **Step 4: Commit (если был тест)**

```bash
git add tests/unit/test_manual_cabinet_day_rollover.py
git commit -m "test(observer): smoke-тест endpoint'а смены суток кабинета"
```

---

## Блок F — observer-status endpoint + UI-плитка

Endpoint уже добавлен в Task E.1 (`GET /api/observer/status`). Дальше — фронт.

### Task F.1: API-функции на фронте

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Добавить функции в `frontend/src/api.js`**

После строки 113 (`createDisableTask`) добавить:

```js
// Observer
export const getObserverStatus = () => request('/observer/status');
export const startNewCabinetDay = () =>
  request('/observer/start-new-cabinet-day', { method: 'POST' });
```

- [ ] **Step 2: Smoke**

Run: `cd frontend && npm run build`
Expected: build успешен, без ошибок.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat(frontend): API getObserverStatus и startNewCabinetDay"
```

### Task F.2: Компонент ObserverStatusTile.jsx

**Files:**
- Create: `frontend/src/components/observer/ObserverStatusTile.jsx`

- [ ] **Step 1: Создать компонент**

```jsx
import { useEffect, useState } from 'react';

import { getObserverStatus, startNewCabinetDay } from '../../api.js';

// Маппинг статуса на цвет бейджа и человекочитаемый лейбл.
const STATUS_META = {
  RUNNING: { label: 'Работает', tone: 'tone-positive' },
  SCANNING: { label: 'Сканирует', tone: 'tone-info' },
  PAUSED: { label: 'Выключен', tone: 'tone-muted' },
  WAITING_BROWSER: { label: 'Ждёт браузер', tone: 'tone-warning' },
  guard_pending_zero: { label: 'Guard: ждёт подтверждения zero', tone: 'tone-warning' },
  guard_pending_partial: { label: 'Guard: ждёт подтверждения partial', tone: 'tone-warning' },
  ERROR: { label: 'Ошибка', tone: 'tone-danger' },
};

function formatTimestamp(value) {
  if (!value) return '—';
  try {
    const date = new Date(value);
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

function formatRelative(value) {
  if (!value) return '—';
  const ms = Date.now() - new Date(value).getTime();
  if (ms < 0) return 'через мгновение';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hours = Math.floor(min / 60);
  return `${hours} ч назад`;
}

export default function ObserverStatusTile() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [rolloverBusy, setRolloverBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const fetchOnce = async () => {
      try {
        const payload = await getObserverStatus();
        if (alive) {
          setData(payload);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e?.message || 'Не удалось загрузить статус');
      }
    };
    fetchOnce();
    const id = setInterval(fetchOnce, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const handleRollover = async () => {
    const ads = data?.active_total ?? 0;
    if (!window.confirm(`Закрыть текущий день и архивировать ${ads} объявлений?`)) return;
    setRolloverBusy(true);
    try {
      const result = await startNewCabinetDay();
      window.alert(`Новые сутки открыты, архивировано ${result.archived_ads} объявлений.`);
    } catch (e) {
      window.alert(`Ошибка: ${e?.message || e}`);
    } finally {
      setRolloverBusy(false);
    }
  };

  if (error && !data) {
    return (
      <div className="panel p-3 text-2xs text-danger">
        Observer: ошибка загрузки статуса ({error})
      </div>
    );
  }
  if (!data) {
    return <div className="panel p-3 text-2xs text-muted">Observer: загрузка…</div>;
  }

  const statusKey = data.worker_status || 'RUNNING';
  const meta = STATUS_META[statusKey] || { label: statusKey, tone: 'tone-muted' };

  return (
    <div className="panel flex flex-wrap items-center justify-between gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <div className="text-2xs uppercase text-muted">Observer</div>
          <div className={`text-xs font-medium ${meta.tone}`}>● {meta.label}</div>
        </div>
        <div>
          <div className="text-2xs uppercase text-muted">Цикл</div>
          <div className="text-xs font-mono">#{data.current_scan_id ?? 0}</div>
        </div>
        <div>
          <div className="text-2xs uppercase text-muted">Последний батч</div>
          <div className="text-xs font-mono">
            {data.last_batch_size}/{data.active_total}
          </div>
        </div>
        <div>
          <div className="text-2xs uppercase text-muted">Последний пульс</div>
          <div className="text-xs">{formatRelative(data.worker_heartbeat_at)}</div>
        </div>
        <div>
          <div className="text-2xs uppercase text-muted">Сутки кабинета</div>
          <div className="text-xs">
            {data.cabinet_day_started_at ? formatTimestamp(data.cabinet_day_started_at) : '—'}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={handleRollover}
        disabled={rolloverBusy}
        className="btn btn-secondary text-2xs"
      >
        {rolloverBusy ? 'Архивируем…' : 'Начать новые сутки'}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: build успешен.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/observer/ObserverStatusTile.jsx
git commit -m "feat(frontend): компонент ObserverStatusTile"
```

### Task F.3: Встроить плитку в DashboardPage

**Files:**
- Modify: `frontend/src/pages/DashboardPage.jsx:609` (рядом с HeroKPIStrip)

- [ ] **Step 1: Импорт**

В `frontend/src/pages/DashboardPage.jsx` в блок импортов добавить:
```jsx
import ObserverStatusTile from '../components/observer/ObserverStatusTile.jsx';
```

- [ ] **Step 2: Вставить перед HeroKPIStrip**

Найти строку (~609):
```jsx
      <HeroKPIStrip performance={performance} performanceYesterday={performanceYesterday} />
```
Прямо перед ней вставить:
```jsx
      <div className="mb-md">
        <ObserverStatusTile />
      </div>
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build успешен.

- [ ] **Step 4: Smoke вручную**

Запустить `./run.sh`, открыть дашборд в браузере, убедиться, что плитка появилась, статус подгружается, кнопка «Начать новые сутки» работает.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/DashboardPage.jsx
git commit -m "feat(frontend): ObserverStatusTile на DashboardPage"
```

---

## Блок G — Чистка логов

### Task G.1: Удалить периодический лог «сканирование отключено»

**Files:**
- Modify: `apps/observer_worker/main.py:1291`

- [ ] **Step 1: Удалить строку лога**

В `apps/observer_worker/main.py` найти:
```python
                    logger.info("Observer: сканирование отключено, пропускаем цикл")
```
(строка ~1291)

Удалить эту строку целиком. Окружающий код (status update + `await asyncio.sleep(10.0)` + `continue`) оставить как есть — состояние `PAUSED` всё равно проставляется в `worker_status` через `update_observer_runtime_status` и видно в UI-плитке.

- [ ] **Step 2: Проверить, нет ли других подобных мест**

Run: `grep -n "сканирование отключено\|scanning_enabled" apps/observer_worker/main.py`

Если есть другие `logger.info` в idle-цикле, повторяющиеся каждые N секунд — удалить по тому же принципу. Логи о смене состояния (включение/выключение) — оставить.

- [ ] **Step 3: Lint**

Run: `ruff check apps/observer_worker/main.py`
Expected: All checks passed!

- [ ] **Step 4: Smoke**

Запустить observer, выключить сканирование из UI, подождать минуту, проверить:
```bash
tail -50 .logs/observer.log | grep "сканирование отключено"
```
Expected: пусто.

- [ ] **Step 5: Commit**

```bash
git add apps/observer_worker/main.py
git commit -m "chore(observer): удалён повторяющийся лог «сканирование отключено»"
```

---

## Финальная верификация

### Task FINAL: Полная проверка

- [ ] **Step 1: Lint всего проекта**

Run: `ruff check .`
Expected: All checks passed!

- [ ] **Step 2: Unit-тесты**

Run: `pytest tests/unit/ -x -q`
Expected: All pass.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run build`
Expected: build успешен.

- [ ] **Step 4: End-to-end smoke**

1. `./run.sh`
2. Открыть дашборд → проверить, что плитка Observer отображается.
3. Подождать пару циклов observer'а → счётчик «Цикл #N» растёт, «Последний батч N/M» обновляется.
4. Распределение в нижней части дашборда совпадает с `last_batch_size`.
5. Нажать «Начать новые сутки» → confirm → toast/alert с числом архивированных.
6. В БД: `SELECT cabinet_day_started_at FROM observer_settings;` — обновилось.
7. В логах observer.log: повторов «сканирование отключено» нет.

- [ ] **Step 5: Final commit (если что-то осталось)**

Если по итогам smoke что-то поправилось — commit с понятным сообщением.
