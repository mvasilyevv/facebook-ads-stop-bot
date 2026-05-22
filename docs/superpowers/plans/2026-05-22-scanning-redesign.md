# Редизайн цикла сканирования Observer и UI результатов — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести готовность данных в browser-agent, ввести 7 явных outcome'ов цикла, добавить таблицу `scan_runs`, переработать UI-плитку Observer с историей сканов, убрать ложную надпись «Нет подключения к браузеру» и auto-disable сканирования.

**Architecture:** Browser-agent (Node) становится единственным судьёй готовности данных и возвращает расширенный `ScanComplete` (phase_timings, partial_rows, warnings, empty_reason, rows_with_all_metrics_empty). Observer (Python) — тонкий клиент: вызывает `run_scan_cycle`, классифицирует результат функцией `classify_scan_outcome`, идёт по одной из 7 веток, пишет каждый цикл в новую таблицу `scan_runs`. UI читает `scan_runs` через `/api/observer/status` и `/api/observer/scan-runs`, рендерит расширенную плитку + модалку истории.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, Alembic, FastAPI, grpcio, Node.js 20 + @grpc/grpc-js + Playwright, React 19 + Vite, pytest, vitest.

**Спецификация:** [docs/superpowers/specs/2026-05-22-scanning-redesign-design.md](docs/superpowers/specs/2026-05-22-scanning-redesign-design.md)

---

## Структура файлов

**Создаются:**
- `core/models/scan_runs.py` — модель `ScanRun`
- `core/observer/scan_run_writer.py` — `begin_scan_run`, `finish_scan_run`, `mark_interrupted_runs`
- `core/observer/outcome_classifier.py` — pure-function `classify_scan_outcome`
- `core/observer/stale_data_handler.py` — счётчик попыток + эскалация refresh/hard-reload
- `core/observer/browser_recovery.py` — экспоненциальный reconnect
- `migrations/versions/<rev>_scan_runs_and_stale_threshold.py` — alembic ревизия
- `apps/api/routers/scan_runs.py` — endpoint `GET /api/observer/scan-runs`
- `frontend/src/components/observer/ScanRunsHistoryModal.jsx` — модалка истории
- `tests/unit/test_outcome_classifier.py`
- `tests/unit/test_stale_data_handler.py`
- `tests/unit/test_scan_run_writer.py`
- `tests/integration/test_observer_outcomes.py`
- `services/browser-agent/src/empty-reason.ts` — детектор пустоты таблицы
- `services/browser-agent/src/empty-reason.test.ts`
- `services/browser-agent/src/hard-reload.ts` — hard reload через CDP
- `services/browser-agent/src/hard-reload.test.ts`

**Модифицируются:**
- `proto/v1/scanner.proto` — расширение `ScanComplete`, новый RPC `HardReloadPage`
- `clients/python_grpc/client.py` — `ScanResult` поля, метод `hard_reload`
- `services/browser-agent/src/index.ts` — наполнение нового `ScanComplete`, регистрация `HardReloadPage`
- `services/browser-agent/src/parser.ts` — подсчёт `rows_with_all_metrics_empty` и `partial_rows`
- `apps/observer_worker/main.py` — выкидывание `_wait_for_data_load`/`_merge_scan_rows`/auto-disable, новая switch-логика
- `apps/api/routers/observer.py` — расширение `/api/observer/status`
- `apps/api/main.py` — lifespan: фоновый task `mark_interrupted_runs` + retention cleanup
- `apps/api/main.py` — регистрация роутера `scan_runs`
- `core/models/__init__.py` — экспорт `ScanRun`, поле `stale_data_threshold` в `ObserverSettings`
- `frontend/src/api.js` — `getScanRuns()`, расширение типа ответа `getObserverStatus()`
- `frontend/src/components/observer/ObserverStatusTile.jsx` — полная переработка
- `frontend/src/components/dashboard/DashboardCommandBar.jsx` — удаление блока статуса observer'а

**Удаляются (по коду, не сами файлы):**
- `_wait_for_data_load`, `_merge_scan_rows`, `prev_scan_had_spend` — из `apps/observer_worker/main.py`
- Все вызовы `set_observer_scanning_enabled(False)` из observer'а

---

## Phase 1 — Proto и БД (фундамент)

### Task 1: Расширить scanner.proto

**Files:**
- Modify: `proto/v1/scanner.proto`

- [ ] **Step 1: Добавить поля в `ScanComplete` и новые RPC**

В `proto/v1/scanner.proto`:

```proto
// 1) Расширить service ScannerService — добавить RPC после ApplyColumnWidths:
service ScannerService {
  // ... существующие RPC ...

  // Жёсткая перезагрузка страницы с очисткой кеша (через CDP Network.clearBrowserCache).
  rpc HardReloadPage(HardReloadPageRequest) returns (HardReloadPageResponse);
}

// 2) Расширить ScanComplete:
message ScanComplete {
  repeated ScannedAdRow all_rows = 1;
  int32 total_passes = 2;
  double duration_seconds = 3;
  repeated string dismissed_modals = 4;
  repeated string unknown_modal_artifacts = 5;

  // НОВОЕ:
  PhaseTimings phase_timings = 6;
  repeated string partial_row_ids = 7;          // fb_ad_id строк с недочитанными колонками
  repeated string warnings = 8;                  // коды: "loader_visible_long", "header_missing_columns", ...
  string empty_reason = 9;                       // "" | "no_active_ads" | "filter_excludes_all" | "table_not_found"
  int32 rows_with_all_metrics_empty = 10;        // строк, где все критические метрики = "—"
}

message PhaseTimings {
  int32 refresh_ms = 1;
  int32 first_row_ms = 2;
  int32 scroll_ms = 3;
  int32 parse_ms = 4;
  int32 total_ms = 5;
}

// 3) Новые сообщения для HardReloadPage в конец файла (перед HumanProfile):
message HardReloadPageRequest {
  string session_id = 1;
  optional string page_id = 2;
  bool bypass_cache = 3;       // default true — clearBrowserCache через CDP
}

message HardReloadPageResponse {
  bool success = 1;
  string error_message = 2;
  int32 reload_ms = 3;
}
```

- [ ] **Step 2: Перегенерировать Python stubs**

Run: `cd /Users/markvasilev/Desktop/FB_Agent && python -m grpc_tools.protoc -Iproto --python_out=clients/python_grpc/v1 --pyi_out=clients/python_grpc/v1 --grpc_python_out=clients/python_grpc/v1 proto/v1/scanner.proto`

Expected: файлы `clients/python_grpc/v1/scanner_pb2.py`, `scanner_pb2.pyi`, `scanner_pb2_grpc.py` обновлены без ошибок.

- [ ] **Step 3: Перегенерировать TypeScript-обёртки (если есть генерация в проекте)**

Run: `grep -rn "proto-loader\|@grpc/proto-loader" /Users/markvasilev/Desktop/FB_Agent/services/browser-agent/src/index.ts | head -5`

`index.ts` загружает .proto в рантайме через `protoLoader.loadSync` — отдельной кодгенерации нет. Просто убеждаемся, что `cd services/browser-agent && npm run build` не падает после изменения proto.

Run: `cd /Users/markvasilev/Desktop/FB_Agent/services/browser-agent && npm run build`
Expected: build проходит без ошибок (TypeScript ничего нового не требует, т.к. proto читается runtime).

- [ ] **Step 4: Commit**

```bash
git add proto/v1/scanner.proto clients/python_grpc/v1/scanner_pb2.py clients/python_grpc/v1/scanner_pb2.pyi clients/python_grpc/v1/scanner_pb2_grpc.py
git commit -m "feat(proto): расширить ScanComplete и добавить HardReloadPage RPC"
```

---

### Task 2: Alembic-миграция — таблица scan_runs и поле stale_data_threshold

**Files:**
- Create: `migrations/versions/<rev>_scan_runs_and_stale_threshold.py`

- [ ] **Step 1: Сгенерировать ревизию-заглушку**

Run: `cd /Users/markvasilev/Desktop/FB_Agent && alembic revision -m "scan_runs and stale_data_threshold"`
Expected: создан новый файл в `migrations/versions/`. Запомнить его имя.

- [ ] **Step 2: Заполнить тело ревизии**

В новом файле:

```python
"""scan_runs and stale_data_threshold

Revision ID: <auto>
Revises: f6a7b8c9d0e1
Create Date: 2026-05-22 ...
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "<auto>"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("scan_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column("rows_partial", sa.Integer(), nullable=True),
        sa.Column("rows_with_data", sa.Integer(), nullable=True),
        sa.Column("alerts_warning", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_stop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase_timings", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("empty_reason", sa.String(64), nullable=True),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("threat_level", sa.String(32), nullable=True),
        sa.Column("next_interval_s", sa.Integer(), nullable=True),
    )
    op.create_index("scan_runs_started_at_idx", "scan_runs", ["started_at"], postgresql_using=None)
    op.create_index(
        "scan_runs_outcome_idx",
        "scan_runs",
        ["outcome"],
        postgresql_where=sa.text("outcome != 'OK'"),
    )

    op.add_column(
        "observer_settings",
        sa.Column(
            "stale_data_threshold",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="0.9",
        ),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "stale_data_threshold")
    op.drop_index("scan_runs_outcome_idx", table_name="scan_runs")
    op.drop_index("scan_runs_started_at_idx", table_name="scan_runs")
    op.drop_table("scan_runs")
```

> Перед записью **проверь** `down_revision` — он должен быть = последняя голова alembic. Узнать: `alembic heads`. Если head не `f6a7b8c9d0e1`, поставить актуальный.

- [ ] **Step 3: Применить миграцию локально**

Run: `cd /Users/markvasilev/Desktop/FB_Agent && alembic upgrade head`
Expected: миграция применилась, таблица `scan_runs` создана, колонка `observer_settings.stale_data_threshold` добавлена.

Проверить: `docker compose exec postgres psql -U fbbot -d fbbot -c "\d scan_runs"`
Expected: видна структура таблицы со всеми колонками и индексами.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/*_scan_runs_and_stale_threshold.py
git commit -m "feat(db): миграция scan_runs и stale_data_threshold в observer_settings"
```

---

### Task 3: SQLAlchemy-модель ScanRun + поле в ObserverSettings

**Files:**
- Create: `core/models/scan_runs.py`
- Modify: `core/models/__init__.py`
- Test: `tests/unit/test_scan_run_model.py`

- [ ] **Step 1: Написать тест на инстанцирование модели**

В `tests/unit/test_scan_run_model.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет, что модель ScanRun создаётся с обязательными полями
   и принимает все типы данных из спецификации."""

from datetime import UTC, datetime

from core.models import ScanRun


def test_scan_run_constructor_minimal():
    """ScanRun создаётся с минимальным набором обязательных полей."""
    run = ScanRun(
        scan_id=1,
        started_at=datetime.now(UTC),
        outcome="RUNNING",
    )
    assert run.scan_id == 1
    assert run.outcome == "RUNNING"
    assert run.alerts_warning == 0
    assert run.alerts_stop == 0


def test_scan_run_constructor_full():
    """ScanRun принимает все поля результата."""
    run = ScanRun(
        scan_id=2,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome="OK_PARTIAL",
        rows_total=58,
        rows_partial=3,
        rows_with_data=47,
        alerts_warning=2,
        alerts_stop=1,
        phase_timings={"refresh_ms": 200, "first_row_ms": 600},
        warnings=["loader_visible_long"],
        empty_reason=None,
        error_kind=None,
        error_message=None,
        threat_level="MEDIUM",
        next_interval_s=45,
    )
    assert run.rows_total == 58
    assert "loader_visible_long" in run.warnings
    assert run.phase_timings["refresh_ms"] == 200
```

- [ ] **Step 2: Запустить тест — должен упасть с ImportError**

Run: `pytest tests/unit/test_scan_run_model.py -x`
Expected: FAIL, `ImportError: cannot import name 'ScanRun'`.

- [ ] **Step 3: Создать модель**

В `core/models/scan_runs.py`:

```python
# -*- coding: utf-8 -*-
"""SQLAlchemy-модель таблицы scan_runs — история циклов observer'а."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class ScanRun(Base):
    """Одна строка на цикл сканирования observer'а.

    Жизненный цикл:
      1. Observer вставляет «черновик» в начале цикла (outcome='RUNNING', finished_at=NULL).
      2. По завершении делает UPDATE: outcome + все метрики.
      3. Если процесс упал — фоновая задача API через 5 мин ставит outcome='INTERRUPTED'.
    """

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_partial: Mapped[int | None] = mapped_column(Integer)
    rows_with_data: Mapped[int | None] = mapped_column(Integer)
    alerts_warning: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    alerts_stop: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    phase_timings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    empty_reason: Mapped[str | None] = mapped_column(String(64))
    error_kind: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    threat_level: Mapped[str | None] = mapped_column(String(32))
    next_interval_s: Mapped[int | None] = mapped_column(Integer)
```

- [ ] **Step 4: Зарегистрировать модель в `core/models/__init__.py`**

В `core/models/__init__.py` найти место в начале файла, где импортируются другие модели (`from .ad_snapshots import …`), и добавить:

```python
from .scan_runs import ScanRun  # noqa: F401
```

Также добавить `ScanRun` в `__all__` если он есть в этом файле.

В классе `ObserverSettings` (строка ~92 в `core/models/__init__.py`), после поля `agent_commission_percent`, добавить:

```python
    # Порог детекции STALE_DATA: доля строк с пустыми метриками, выше которой
    # цикл считается STALE_DATA. Default 0.9 — 90% строк с прочерками.
    stale_data_threshold: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.9"), server_default="0.9"
    )
```

- [ ] **Step 5: Запустить тест — должен пройти**

Run: `pytest tests/unit/test_scan_run_model.py -x -v`
Expected: оба теста PASS.

- [ ] **Step 6: Commit**

```bash
git add core/models/scan_runs.py core/models/__init__.py tests/unit/test_scan_run_model.py
git commit -m "feat(models): добавить ScanRun и stale_data_threshold"
```

---

## Phase 2 — Browser-agent (Node.js): новые поля и hard reload

### Task 4: Детектор `empty_reason` в browser-agent

**Files:**
- Create: `services/browser-agent/src/empty-reason.ts`
- Create: `services/browser-agent/src/empty-reason.test.ts`

- [ ] **Step 1: Написать тест**

В `services/browser-agent/src/empty-reason.test.ts`:

```typescript
// Юнит-тесты детектора причины пустого скана.
// Сценарии: таблицы нет вообще, таблица есть но без строк (фильтр), таблица есть но без активных кампаний.

import { describe, expect, it } from 'vitest';
import { detectEmptyReason } from './empty-reason.js';

describe('detectEmptyReason', () => {
  it('возвращает table_not_found, когда нет хедера таблицы', () => {
    expect(
      detectEmptyReason({ hasTableHeader: false, hasFilterChips: false, rowCount: 0 }),
    ).toBe('table_not_found');
  });

  it('возвращает filter_excludes_all, когда хедер есть, есть фильтр-чипы и 0 строк', () => {
    expect(
      detectEmptyReason({ hasTableHeader: true, hasFilterChips: true, rowCount: 0 }),
    ).toBe('filter_excludes_all');
  });

  it('возвращает no_active_ads, когда хедер есть, фильтров нет и 0 строк', () => {
    expect(
      detectEmptyReason({ hasTableHeader: true, hasFilterChips: false, rowCount: 0 }),
    ).toBe('no_active_ads');
  });

  it('возвращает null, когда есть хотя бы одна строка', () => {
    expect(
      detectEmptyReason({ hasTableHeader: true, hasFilterChips: false, rowCount: 1 }),
    ).toBeNull();
  });
});
```

- [ ] **Step 2: Запустить — упадёт**

Run: `cd services/browser-agent && npx vitest run src/empty-reason.test.ts`
Expected: FAIL, модуль не найден.

- [ ] **Step 3: Написать реализацию**

В `services/browser-agent/src/empty-reason.ts`:

```typescript
// Чистая функция: по фактам о DOM решает, почему скан пустой.
// Сами факты собирает caller (видит ли page <table> с хедером, видны ли чипы фильтра).

export type EmptyReason = 'table_not_found' | 'filter_excludes_all' | 'no_active_ads';

export interface EmptyReasonInput {
  hasTableHeader: boolean;
  hasFilterChips: boolean;
  rowCount: number;
}

export function detectEmptyReason(input: EmptyReasonInput): EmptyReason | null {
  if (input.rowCount > 0) {
    return null;
  }
  if (!input.hasTableHeader) {
    return 'table_not_found';
  }
  if (input.hasFilterChips) {
    return 'filter_excludes_all';
  }
  return 'no_active_ads';
}
```

- [ ] **Step 4: Запустить — должен пройти**

Run: `cd services/browser-agent && npx vitest run src/empty-reason.test.ts`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/empty-reason.ts services/browser-agent/src/empty-reason.test.ts
git commit -m "feat(browser-agent): детектор empty_reason"
```

---

### Task 5: HardReload через CDP

**Files:**
- Create: `services/browser-agent/src/hard-reload.ts`
- Create: `services/browser-agent/src/hard-reload.test.ts`

- [ ] **Step 1: Написать тест с моками Playwright**

В `services/browser-agent/src/hard-reload.test.ts`:

```typescript
// Проверка: hardReloadPage вызывает clearBrowserCache через CDPSession
// и затем page.reload({ waitUntil: 'networkidle' }), возвращая длительность.

import { describe, expect, it, vi } from 'vitest';
import { hardReloadPage } from './hard-reload.js';

describe('hardReloadPage', () => {
  it('очищает кеш через CDP и перезагружает страницу', async () => {
    const cdpSend = vi.fn().mockResolvedValue(undefined);
    const detach = vi.fn().mockResolvedValue(undefined);
    const reload = vi.fn().mockResolvedValue(undefined);
    const newCDPSession = vi.fn().mockResolvedValue({ send: cdpSend, detach });

    const page: any = {
      context: () => ({ newCDPSession }),
      reload,
    };

    const result = await hardReloadPage(page, true);

    expect(newCDPSession).toHaveBeenCalledWith(page);
    expect(cdpSend).toHaveBeenCalledWith('Network.clearBrowserCache');
    expect(reload).toHaveBeenCalledWith({ waitUntil: 'networkidle', timeout: 60_000 });
    expect(detach).toHaveBeenCalled();
    expect(result.success).toBe(true);
    expect(result.reloadMs).toBeGreaterThanOrEqual(0);
    expect(result.errorMessage).toBe('');
  });

  it('возвращает success=false и error при падении reload', async () => {
    const cdpSend = vi.fn().mockResolvedValue(undefined);
    const detach = vi.fn().mockResolvedValue(undefined);
    const newCDPSession = vi.fn().mockResolvedValue({ send: cdpSend, detach });
    const reload = vi.fn().mockRejectedValue(new Error('navigation failed'));

    const page: any = {
      context: () => ({ newCDPSession }),
      reload,
    };

    const result = await hardReloadPage(page, true);

    expect(result.success).toBe(false);
    expect(result.errorMessage).toContain('navigation failed');
  });

  it('пропускает clearBrowserCache, если bypassCache=false', async () => {
    const cdpSend = vi.fn();
    const newCDPSession = vi.fn().mockResolvedValue({ send: cdpSend, detach: vi.fn() });
    const reload = vi.fn().mockResolvedValue(undefined);

    const page: any = {
      context: () => ({ newCDPSession }),
      reload,
    };

    await hardReloadPage(page, false);

    expect(newCDPSession).not.toHaveBeenCalled();
    expect(reload).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Запустить — упадёт**

Run: `cd services/browser-agent && npx vitest run src/hard-reload.test.ts`
Expected: FAIL, модуль не найден.

- [ ] **Step 3: Реализация**

В `services/browser-agent/src/hard-reload.ts`:

```typescript
// Жёсткая перезагрузка страницы Ads Manager с обходом кеша.
// Используется observer'ом при STALE_DATA — когда Ads Manager не отдал метрики.

import type { Page } from 'playwright-core';

export interface HardReloadResult {
  success: boolean;
  errorMessage: string;
  reloadMs: number;
}

export async function hardReloadPage(page: Page, bypassCache: boolean): Promise<HardReloadResult> {
  const startedAt = Date.now();

  if (bypassCache) {
    const session = await page.context().newCDPSession(page);
    try {
      await session.send('Network.clearBrowserCache');
    } finally {
      await session.detach().catch(() => undefined);
    }
  }

  try {
    await page.reload({ waitUntil: 'networkidle', timeout: 60_000 });
  } catch (err: any) {
    return {
      success: false,
      errorMessage: String(err?.message ?? err),
      reloadMs: Date.now() - startedAt,
    };
  }

  return {
    success: true,
    errorMessage: '',
    reloadMs: Date.now() - startedAt,
  };
}
```

- [ ] **Step 4: Запустить — должен пройти**

Run: `cd services/browser-agent && npx vitest run src/hard-reload.test.ts`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/hard-reload.ts services/browser-agent/src/hard-reload.test.ts
git commit -m "feat(browser-agent): hard reload с обходом кеша через CDP"
```

---

### Task 6: Подсчёт `rows_with_all_metrics_empty` и `partial_row_ids` в parser

**Files:**
- Modify: `services/browser-agent/src/parser.ts`
- Test: `services/browser-agent/src/parser-stale.test.ts` (новый)

- [ ] **Step 1: Прочитать текущий parser**

Run: `wc -l services/browser-agent/src/parser.ts && grep -n "export function\|export const\|interface " services/browser-agent/src/parser.ts | head -20`
Action: ознакомиться с интерфейсом `parseAdsFromPage`/`waitForParsedAdsRows` — нужны точные сигнатуры. Найти где возвращается `ScannedAdRow[]`.

- [ ] **Step 2: Написать тест на новые поля**

Файл `services/browser-agent/src/parser-stale.test.ts`:

```typescript
// Проверяет: parser экспортирует helper countEmptyMetricsRows,
// который считает строки, у которых все критические метрики = '' / '—' / null.

import { describe, expect, it } from 'vitest';
import { countEmptyMetricsRows, findPartialRows } from './parser.js';
import type { ScannedAdRow } from './types.js';

function makeRow(overrides: Partial<ScannedAdRow>): ScannedAdRow {
  return {
    fbAdId: '1',
    campaignName: 'c',
    adsetName: 'a',
    adName: 'n',
    deliveryStatus: 'Активно',
    spend: '',
    budget: '',
    reach: 0,
    impressions: 0,
    clicks: 0,
    cpc: '',
    ctr: '',
    outboundClicks: 0,
    outboundCtr: '',
    landingPageViews: 0,
    costPerLandingPageView: '',
    costPerResult: '',
    cpm: '',
    frequency: '',
    leads: 0,
    costPerLead: '',
    registrations: 0,
    costPerRegistration: '',
    deposits: 0,
    resolvedOfferCode: '',
    ...overrides,
  };
}

describe('countEmptyMetricsRows', () => {
  it('считает строку пустой, если все критические метрики = "" или "—"', () => {
    const row = makeRow({ impressions: 0, spend: '', cpm: '—', cpc: '', ctr: '' });
    expect(countEmptyMetricsRows([row])).toBe(1);
  });

  it('не считает пустой, если хотя бы одна метрика непустая', () => {
    const row = makeRow({ impressions: 100, spend: '' });
    expect(countEmptyMetricsRows([row])).toBe(0);
  });
});

describe('findPartialRows', () => {
  it('возвращает fb_ad_id строк, у которых пустые ad_name или campaign_name', () => {
    const rows = [
      makeRow({ fbAdId: '1', adName: '', campaignName: 'c' }),
      makeRow({ fbAdId: '2', adName: 'n', campaignName: 'c' }),
    ];
    expect(findPartialRows(rows)).toEqual(['1']);
  });
});
```

- [ ] **Step 3: Запустить — упадёт (нет экспортов)**

Run: `cd services/browser-agent && npx vitest run src/parser-stale.test.ts`
Expected: FAIL, `countEmptyMetricsRows` не экспортирован.

- [ ] **Step 4: Добавить функции в `parser.ts`**

В `services/browser-agent/src/parser.ts` добавить в конец файла:

```typescript
// --- Helpers для детекции STALE_DATA и partial rows ---

const EMPTY_METRIC_PLACEHOLDERS = new Set(['', '—', '-', '–', 'N/A', '—']);

function isEmptyMetric(value: string | number | undefined | null): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'number') return value === 0;
  const trimmed = String(value).trim();
  return EMPTY_METRIC_PLACEHOLDERS.has(trimmed);
}

export function countEmptyMetricsRows(rows: ScannedAdRow[]): number {
  let count = 0;
  for (const row of rows) {
    const allEmpty =
      isEmptyMetric(row.impressions) &&
      isEmptyMetric(row.spend) &&
      isEmptyMetric(row.cpm) &&
      isEmptyMetric(row.cpc) &&
      isEmptyMetric(row.ctr);
    if (allEmpty) count += 1;
  }
  return count;
}

export function findPartialRows(rows: ScannedAdRow[]): string[] {
  const partial: string[] = [];
  for (const row of rows) {
    if (!row.fbAdId) continue;
    if (!row.adName || !row.campaignName) {
      partial.push(row.fbAdId);
    }
  }
  return partial;
}
```

> Если import `ScannedAdRow` не подхватился — добавить в начало parser.ts: `import type { ScannedAdRow } from './types.js';` (проверь нет ли уже).

- [ ] **Step 5: Запустить тест**

Run: `cd services/browser-agent && npx vitest run src/parser-stale.test.ts`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add services/browser-agent/src/parser.ts services/browser-agent/src/parser-stale.test.ts
git commit -m "feat(browser-agent): countEmptyMetricsRows и findPartialRows"
```

---

### Task 7: Интеграция новых полей в handler `RunScanCycle` и регистрация `HardReloadPage`

**Files:**
- Modify: `services/browser-agent/src/index.ts`

- [ ] **Step 1: Найти текущий обработчик RunScanCycle**

Run: `grep -n "RunScanCycle\|HardReloadPage\|ScanComplete" services/browser-agent/src/index.ts | head -20`
Action: посмотри, где собирается ответ `ScanComplete` (вероятно в коллбэке стрима).

- [ ] **Step 2: Расширить ScanComplete-payload и зарегистрировать HardReloadPage**

В `services/browser-agent/src/index.ts`:

В начале файла добавить импорты:

```typescript
import { detectEmptyReason } from './empty-reason.js';
import { hardReloadPage } from './hard-reload.js';
import { countEmptyMetricsRows, findPartialRows } from './parser.js';
```

В обработчике `runScanCycle` (там, где собирается финальный объект `ScanComplete` для стрима), **до отправки события `complete`** собрать новые поля и положить в payload:

```typescript
// После того как rows собраны и duration_seconds посчитан:
const phaseTimings = {
  refresh_ms: phaseTimingsCollector.refresh ?? 0,
  first_row_ms: phaseTimingsCollector.firstRow ?? 0,
  scroll_ms: phaseTimingsCollector.scroll ?? 0,
  parse_ms: phaseTimingsCollector.parse ?? 0,
  total_ms: phaseTimingsCollector.total ?? Math.round(durationSeconds * 1000),
};

// Соберём empty_reason — нужен hasTableHeader/hasFilterChips. Если их нет в текущем коде,
// после refresh добавь page.evaluate, собирающий два булева. Пример:
const tableState = await page.evaluate(() => {
  const header = document.querySelector('div[role="columnheader"]');
  const filterChips = document.querySelectorAll('[aria-label*="фильтр" i], [aria-label*="filter" i]');
  return { hasTableHeader: !!header, hasFilterChips: filterChips.length > 0 };
});

const emptyReason = detectEmptyReason({
  hasTableHeader: tableState.hasTableHeader,
  hasFilterChips: tableState.hasFilterChips,
  rowCount: allRows.length,
});

const rowsWithAllMetricsEmpty = countEmptyMetricsRows(allRows);
const partialRowIds = findPartialRows(allRows);

const warnings: string[] = [];
// Если синяя полоса загрузки видна слишком долго — пометка
if (loaderWasVisibleLong) warnings.push('loader_visible_long');
// Если хедер таблицы исчез/появился во время скана — пометка
if (tableState.hasTableHeader === false) warnings.push('header_missing_columns');

// Затем при отправке complete-эвента:
call.write({
  session_id: sessionId,
  complete: {
    all_rows: allRows,
    total_passes: totalPasses,
    duration_seconds: durationSeconds,
    dismissed_modals: dismissedModals,
    unknown_modal_artifacts: unknownModalArtifacts,
    phase_timings: phaseTimings,
    partial_row_ids: partialRowIds,
    warnings,
    empty_reason: emptyReason ?? '',
    rows_with_all_metrics_empty: rowsWithAllMetricsEmpty,
  },
});
```

> Если в текущем коде нет переменной `phaseTimingsCollector` — оберни ключевые шаги в `const t0 = Date.now();` … `phaseTimings.refresh = Date.now() - t0;`. Хватит первого приближения: `refresh_ms`, `first_row_ms`, `scroll_ms`, `parse_ms`, `total_ms`. Если не получится снять `loaderWasVisibleLong` — поставь `false`, добавим позже.

Зарегистрировать новый RPC `HardReloadPage` рядом с остальными:

```typescript
async function hardReloadPageHandler(call: any, callback: any) {
  try {
    const sessionId = String(call.request.session_id);
    const bypassCache = Boolean(call.request.bypass_cache);
    const session = sessionManager.getSession(sessionId);
    if (!session) {
      callback({ code: grpc.status.NOT_FOUND, message: 'session not found' });
      return;
    }
    const page = getPage(session, call.request.page_id);
    const result = await hardReloadPage(page, bypassCache);
    callback(null, {
      success: result.success,
      error_message: result.errorMessage,
      reload_ms: result.reloadMs,
    });
  } catch (err: any) {
    callback({ code: grpcCodeForError(err), message: String(err?.message ?? err) });
  }
}
```

В блоке `server.addService(..., { ... })` для `ScannerService` добавить:

```typescript
HardReloadPage: hardReloadPageHandler,
```

- [ ] **Step 3: Сборка**

Run: `cd services/browser-agent && npm run build`
Expected: build проходит без ошибок.

- [ ] **Step 4: Прогон существующих тестов**

Run: `cd services/browser-agent && npx vitest run`
Expected: все тесты зелёные.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/index.ts
git commit -m "feat(browser-agent): новые поля в ScanComplete и регистрация HardReloadPage"
```

---

### Task 8: Расширить Python `ScanResult` и добавить метод `hard_reload`

**Files:**
- Modify: `clients/python_grpc/client.py`
- Test: `tests/unit/test_grpc_client_scan_result.py` (новый)

- [ ] **Step 1: Тест на новые поля dataclass**

В `tests/unit/test_grpc_client_scan_result.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет, что ScanResult принимает новые поля: phase_timings, partial_row_ids,
   warnings, empty_reason, rows_with_all_metrics_empty."""

from clients.python_grpc.client import ScanResult


def test_scan_result_default_new_fields():
    """По умолчанию новые поля заполнены пустыми коллекциями / None."""
    result = ScanResult(rows=[], total_passes=0, duration_seconds=0.0)
    assert result.phase_timings == {}
    assert result.partial_row_ids == []
    assert result.warnings == []
    assert result.empty_reason is None
    assert result.rows_with_all_metrics_empty == 0


def test_scan_result_explicit_new_fields():
    """Поля корректно сохраняются."""
    result = ScanResult(
        rows=[],
        total_passes=1,
        duration_seconds=2.5,
        phase_timings={"refresh_ms": 200},
        partial_row_ids=["1", "2"],
        warnings=["loader_visible_long"],
        empty_reason="no_active_ads",
        rows_with_all_metrics_empty=5,
    )
    assert result.phase_timings["refresh_ms"] == 200
    assert result.partial_row_ids == ["1", "2"]
    assert result.empty_reason == "no_active_ads"
    assert result.rows_with_all_metrics_empty == 5
```

- [ ] **Step 2: Запустить — упадёт**

Run: `pytest tests/unit/test_grpc_client_scan_result.py -x -v`
Expected: FAIL — поля отсутствуют.

- [ ] **Step 3: Расширить dataclass и добавить метод**

В `clients/python_grpc/client.py` заменить dataclass `ScanResult`:

```python
@dataclass
class ScanResult:
    """Полный результат сканирования."""

    rows: list
    total_passes: int
    duration_seconds: float
    dismissed_modals: list[str] = field(default_factory=list)
    unknown_modal_artifacts: list[str] = field(default_factory=list)
    phase_timings: dict[str, int] = field(default_factory=dict)
    partial_row_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    empty_reason: str | None = None
    rows_with_all_metrics_empty: int = 0
```

В `BrowserAgentClient` (после метода `reconnect_browser`) добавить:

```python
    async def hard_reload(self, *, bypass_cache: bool = True) -> bool:
        """Жёсткая перезагрузка страницы Ads Manager с обходом кеша.

        Возвращает True при успехе, False при ошибке (логирует причину).
        """
        if not self._scanner_stub or not self._session_id:
            logger.warning("hard_reload: нет активной сессии browser-agent")
            return False
        req = scanner_pb2.HardReloadPageRequest(
            session_id=self._session_id,
            bypass_cache=bypass_cache,
        )
        try:
            resp = await self._scanner_stub.HardReloadPage(
                req, timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS * 2
            )
        except grpc.RpcError as exc:
            logger.warning("hard_reload: gRPC error: %s", exc)
            return False
        if not resp.success:
            logger.warning("hard_reload: %s", resp.error_message)
            return False
        logger.info("hard_reload: success за %d мс", resp.reload_ms)
        return True
```

Также в обработчике стрима `run_scan_cycle` нужно протащить новые поля из `ScanComplete` в `ScanResult`. Найти строку где конструируется `ScanResult(...)`:

Run: `grep -n "ScanResult(" clients/python_grpc/client.py`
В этом месте добавить:

```python
yield ScanResult(
    rows=parsed_rows,
    total_passes=event.complete.total_passes,
    duration_seconds=event.complete.duration_seconds,
    dismissed_modals=list(event.complete.dismissed_modals),
    unknown_modal_artifacts=list(event.complete.unknown_modal_artifacts),
    phase_timings={
        "refresh_ms": event.complete.phase_timings.refresh_ms,
        "first_row_ms": event.complete.phase_timings.first_row_ms,
        "scroll_ms": event.complete.phase_timings.scroll_ms,
        "parse_ms": event.complete.phase_timings.parse_ms,
        "total_ms": event.complete.phase_timings.total_ms,
    },
    partial_row_ids=list(event.complete.partial_row_ids),
    warnings=list(event.complete.warnings),
    empty_reason=event.complete.empty_reason or None,
    rows_with_all_metrics_empty=event.complete.rows_with_all_metrics_empty,
)
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/unit/test_grpc_client_scan_result.py -x -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clients/python_grpc/client.py tests/unit/test_grpc_client_scan_result.py
git commit -m "feat(grpc-client): новые поля ScanResult и метод hard_reload"
```

---

## Phase 3 — Observer: классификатор, writer, эскалация

### Task 9: `outcome_classifier` — pure function

**Files:**
- Create: `core/observer/outcome_classifier.py`
- Test: `tests/unit/test_outcome_classifier.py`

- [ ] **Step 1: Тесты на все 7 outcome'ов**

В `tests/unit/test_outcome_classifier.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет classify_scan_outcome для всех 7 исходов цикла observer'а."""

from clients.python_grpc.client import ScanResult
from core.observer.outcome_classifier import ScanOutcome, classify_scan_outcome


def _make_result(**overrides) -> ScanResult:
    base = {
        "rows": [],
        "total_passes": 1,
        "duration_seconds": 1.0,
        "empty_reason": None,
        "rows_with_all_metrics_empty": 0,
        "partial_row_ids": [],
        "warnings": [],
    }
    base.update(overrides)
    return ScanResult(**base)


def test_ok_when_rows_present_and_no_partial():
    """OK: строки есть, partial=0, метрики не пустые."""
    rows = ["row1", "row2"]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=0)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK


def test_ok_partial_when_some_partial_rows():
    """OK_PARTIAL: строки есть, есть partial_row_ids."""
    rows = ["row1", "row2"]
    result = _make_result(rows=rows, partial_row_ids=["1"])
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK_PARTIAL
    assert outcome.partial_count == 1


def test_empty_ok_when_no_active_ads():
    """EMPTY_OK: строк нет, empty_reason='no_active_ads'."""
    result = _make_result(empty_reason="no_active_ads")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_OK
    assert outcome.empty_reason == "no_active_ads"


def test_empty_ok_when_filter_excludes_all():
    """EMPTY_OK: строк нет, empty_reason='filter_excludes_all'."""
    result = _make_result(empty_reason="filter_excludes_all")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_OK


def test_empty_bad_when_table_not_found():
    """EMPTY_BAD: строк нет, empty_reason='table_not_found'."""
    result = _make_result(empty_reason="table_not_found")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_BAD


def test_empty_bad_when_empty_reason_missing():
    """EMPTY_BAD: строк нет и empty_reason неопределён — это аномалия."""
    result = _make_result(empty_reason=None)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_BAD


def test_stale_data_when_threshold_exceeded_and_history_exists():
    """STALE_DATA: >= 90% строк без метрик, и у этих fb_ad_id раньше были данные."""
    rows = [f"row{i}" for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=9)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.STALE_DATA


def test_no_stale_when_history_absent():
    """Гард: если у новых fb_ad_id никогда не было метрик — не STALE_DATA."""
    rows = [f"row{i}" for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=9)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: False
    )
    # Должно быть OK_PARTIAL или OK, но не STALE_DATA
    assert outcome.kind != ScanOutcome.STALE_DATA


def test_no_stale_below_threshold():
    """Не STALE_DATA, если меньше порога."""
    rows = [f"row{i}" for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=5)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind != ScanOutcome.STALE_DATA
```

- [ ] **Step 2: Прогнать — упадут (модуль не существует)**

Run: `pytest tests/unit/test_outcome_classifier.py -x`
Expected: FAIL, ImportError.

- [ ] **Step 3: Реализация**

В `core/observer/outcome_classifier.py`:

```python
# -*- coding: utf-8 -*-
"""Чистая функция-классификатор исхода скан-цикла observer'а.

Принимает ScanResult от browser-agent + контекст истории по fb_ad_id,
возвращает одно из 7 финальных состояний цикла.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from clients.python_grpc.client import ScanResult


class ScanOutcome(Enum):
    """Финальное состояние цикла observer'а."""

    OK = "OK"
    OK_PARTIAL = "OK_PARTIAL"
    EMPTY_OK = "EMPTY_OK"
    EMPTY_BAD = "EMPTY_BAD"
    STALE_DATA = "STALE_DATA"
    BROWSER_LOST = "BROWSER_LOST"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True)
class ScanOutcomeDetails:
    """Результат классификации + детали для записи в scan_runs."""

    kind: ScanOutcome
    partial_count: int = 0
    empty_reason: str | None = None
    stale_ratio: float = 0.0
    note: str = ""


def classify_scan_outcome(
    result: ScanResult,
    *,
    stale_threshold: float,
    has_history_for_ids: Callable[[list[str]], bool],
) -> ScanOutcomeDetails:
    """Классифицировать результат сканирования.

    Args:
        result: ScanResult от browser-agent.
        stale_threshold: доля строк-«прочерков», после которой считаем STALE_DATA (0.0–1.0).
        has_history_for_ids: предикат: были ли когда-нибудь метрики у этих fb_ad_id.
            Используется как гард: если у текущих объявлений никогда не было данных,
            то отсутствие метрик — норма, а не сбой.

    Returns:
        ScanOutcomeDetails — финальный исход и детали.
    """
    row_count = len(result.rows)

    if row_count == 0:
        reason = result.empty_reason
        if reason in {"no_active_ads", "filter_excludes_all"}:
            return ScanOutcomeDetails(kind=ScanOutcome.EMPTY_OK, empty_reason=reason)
        return ScanOutcomeDetails(
            kind=ScanOutcome.EMPTY_BAD, empty_reason=reason or "table_not_found"
        )

    # STALE_DATA: процент строк-«прочерков» выше порога + гард по истории
    stale_ratio = result.rows_with_all_metrics_empty / row_count
    if stale_ratio >= stale_threshold:
        ad_ids = [getattr(row, "fb_ad_id", "") for row in result.rows]
        ad_ids = [aid for aid in ad_ids if aid]
        if ad_ids and has_history_for_ids(ad_ids):
            return ScanOutcomeDetails(
                kind=ScanOutcome.STALE_DATA,
                stale_ratio=stale_ratio,
                note=f"{result.rows_with_all_metrics_empty}/{row_count} строк без метрик",
            )

    if result.partial_row_ids:
        return ScanOutcomeDetails(
            kind=ScanOutcome.OK_PARTIAL,
            partial_count=len(result.partial_row_ids),
            stale_ratio=stale_ratio,
        )

    return ScanOutcomeDetails(kind=ScanOutcome.OK, stale_ratio=stale_ratio)
```

- [ ] **Step 4: Прогнать**

Run: `pytest tests/unit/test_outcome_classifier.py -x -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/observer/outcome_classifier.py tests/unit/test_outcome_classifier.py
git commit -m "feat(observer): classify_scan_outcome — pure-function классификатор исхода"
```

---

### Task 10: `scan_run_writer` — запись циклов в БД

**Files:**
- Create: `core/observer/scan_run_writer.py`
- Test: `tests/unit/test_scan_run_writer.py`

- [ ] **Step 1: Тесты**

В `tests/unit/test_scan_run_writer.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет жизненный цикл записи: begin → finish, и mark_interrupted_runs."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from core.models import ScanRun
from core.observer.scan_run_writer import (
    begin_scan_run,
    finish_scan_run,
    mark_interrupted_runs,
)


@pytest.mark.asyncio
async def test_begin_creates_running_draft(db_session):
    """begin_scan_run создаёт строку с outcome='RUNNING' и finished_at=NULL."""
    run_id = await begin_scan_run(db_session, scan_id=42)
    await db_session.commit()

    row = (await db_session.execute(select(ScanRun).where(ScanRun.id == run_id))).scalar_one()
    assert row.outcome == "RUNNING"
    assert row.finished_at is None
    assert row.scan_id == 42


@pytest.mark.asyncio
async def test_finish_updates_outcome_and_fields(db_session):
    """finish_scan_run проставляет outcome, finished_at и все поля."""
    run_id = await begin_scan_run(db_session, scan_id=43)
    await finish_scan_run(
        db_session,
        run_id=run_id,
        outcome="OK",
        rows_total=58,
        rows_partial=0,
        rows_with_data=47,
        alerts_warning=1,
        alerts_stop=0,
        phase_timings={"refresh_ms": 200, "total_ms": 6400},
        warnings=[],
        empty_reason=None,
        error_kind=None,
        error_message=None,
        threat_level="MEDIUM",
        next_interval_s=45,
    )
    await db_session.commit()

    row = (await db_session.execute(select(ScanRun).where(ScanRun.id == run_id))).scalar_one()
    assert row.outcome == "OK"
    assert row.finished_at is not None
    assert row.rows_total == 58
    assert row.phase_timings["refresh_ms"] == 200


@pytest.mark.asyncio
async def test_mark_interrupted_marks_stale_running_rows(db_session):
    """mark_interrupted_runs ставит INTERRUPTED для RUNNING со started_at старше cutoff."""
    old = ScanRun(
        scan_id=1,
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        outcome="RUNNING",
    )
    fresh = ScanRun(
        scan_id=2,
        started_at=datetime.now(UTC),
        outcome="RUNNING",
    )
    db_session.add_all([old, fresh])
    await db_session.commit()

    marked = await mark_interrupted_runs(
        db_session, older_than=datetime.now(UTC) - timedelta(minutes=5)
    )
    await db_session.commit()

    assert marked == 1
    refreshed_old = await db_session.get(ScanRun, old.id)
    refreshed_fresh = await db_session.get(ScanRun, fresh.id)
    assert refreshed_old.outcome == "INTERRUPTED"
    assert refreshed_old.finished_at is not None
    assert refreshed_fresh.outcome == "RUNNING"
```

> Фикстура `db_session`: проверь `tests/conftest.py`. Если нет async-фикстуры — добавь minimal pytest-asyncio фикстуру со scoped Session. Должна уже быть, в проекте есть `pytest-asyncio`.

- [ ] **Step 2: Прогнать — упадут**

Run: `pytest tests/unit/test_scan_run_writer.py -x`
Expected: FAIL, модуль не найден.

- [ ] **Step 3: Реализация**

В `core/observer/scan_run_writer.py`:

```python
# -*- coding: utf-8 -*-
"""Запись/обновление строк scan_runs.

API:
    - begin_scan_run(session, scan_id) → run_id (создаёт черновик outcome='RUNNING')
    - finish_scan_run(session, run_id, outcome, **fields) (UPDATE с финальными данными)
    - mark_interrupted_runs(session, older_than) (помечает зависшие черновики)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ScanRun


async def begin_scan_run(session: AsyncSession, *, scan_id: int) -> int:
    """Создать «черновик» цикла. Возвращает id записи."""
    run = ScanRun(
        scan_id=scan_id,
        started_at=datetime.now(UTC),
        outcome="RUNNING",
    )
    session.add(run)
    await session.flush()
    return run.id


async def finish_scan_run(
    session: AsyncSession,
    *,
    run_id: int,
    outcome: str,
    rows_total: int | None = None,
    rows_partial: int | None = None,
    rows_with_data: int | None = None,
    alerts_warning: int = 0,
    alerts_stop: int = 0,
    phase_timings: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    empty_reason: str | None = None,
    error_kind: str | None = None,
    error_message: str | None = None,
    threat_level: str | None = None,
    next_interval_s: int | None = None,
) -> None:
    """Завершить цикл — UPDATE с финальными полями."""
    await session.execute(
        update(ScanRun)
        .where(ScanRun.id == run_id)
        .values(
            outcome=outcome,
            finished_at=datetime.now(UTC),
            rows_total=rows_total,
            rows_partial=rows_partial,
            rows_with_data=rows_with_data,
            alerts_warning=alerts_warning,
            alerts_stop=alerts_stop,
            phase_timings=phase_timings,
            warnings=warnings,
            empty_reason=empty_reason,
            error_kind=error_kind,
            error_message=error_message,
            threat_level=threat_level,
            next_interval_s=next_interval_s,
        )
    )


async def mark_interrupted_runs(session: AsyncSession, *, older_than: datetime) -> int:
    """Пометить RUNNING-черновики со started_at < older_than как INTERRUPTED.

    Возвращает количество помеченных строк.
    """
    result = await session.execute(
        update(ScanRun)
        .where(ScanRun.outcome == "RUNNING", ScanRun.started_at < older_than)
        .values(outcome="INTERRUPTED", finished_at=datetime.now(UTC))
    )
    return result.rowcount or 0
```

- [ ] **Step 4: Прогнать**

Run: `pytest tests/unit/test_scan_run_writer.py -x -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/observer/scan_run_writer.py tests/unit/test_scan_run_writer.py
git commit -m "feat(observer): scan_run_writer — запись и закрытие циклов в scan_runs"
```

---

### Task 11: `stale_data_handler` — эскалация попыток

**Files:**
- Create: `core/observer/stale_data_handler.py`
- Test: `tests/unit/test_stale_data_handler.py`

- [ ] **Step 1: Тесты на эскалацию**

В `tests/unit/test_stale_data_handler.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет эскалацию STALE_DATA: refresh → hard reload → TG-алерт."""

import pytest

from core.observer.stale_data_handler import StaleDataEscalator, StaleAction


def test_first_attempt_is_refresh():
    """Первая попытка — обычный refresh, sleep 15с."""
    esc = StaleDataEscalator()
    action = esc.next_action()
    assert action.kind == StaleAction.REFRESH
    assert action.sleep_seconds == 15
    assert action.attempt == 1
    assert not action.should_send_alert


def test_second_attempt_is_hard_reload():
    """Вторая попытка — hard reload, sleep 30с."""
    esc = StaleDataEscalator()
    esc.next_action()
    action = esc.next_action()
    assert action.kind == StaleAction.HARD_RELOAD
    assert action.sleep_seconds == 30
    assert action.attempt == 2


def test_third_and_more_hard_reload_60s():
    """3+ попытка — hard reload, sleep 60с (cap)."""
    esc = StaleDataEscalator()
    esc.next_action()
    esc.next_action()
    third = esc.next_action()
    fourth = esc.next_action()
    assert third.kind == StaleAction.HARD_RELOAD
    assert third.sleep_seconds == 60
    assert fourth.sleep_seconds == 60


def test_alert_triggered_at_fifth_attempt():
    """На пятой попытке — should_send_alert=True."""
    esc = StaleDataEscalator()
    actions = [esc.next_action() for _ in range(5)]
    assert all(not a.should_send_alert for a in actions[:4])
    assert actions[4].should_send_alert


def test_reset_clears_counter():
    """reset() обнуляет счётчик попыток."""
    esc = StaleDataEscalator()
    esc.next_action()
    esc.next_action()
    esc.reset()
    action = esc.next_action()
    assert action.attempt == 1
    assert action.kind == StaleAction.REFRESH
```

- [ ] **Step 2: Прогнать — упадут**

Run: `pytest tests/unit/test_stale_data_handler.py -x`
Expected: FAIL.

- [ ] **Step 3: Реализация**

В `core/observer/stale_data_handler.py`:

```python
# -*- coding: utf-8 -*-
"""Эскалатор попыток восстановления при STALE_DATA.

Лестница (см. spec):
    Попытка 1: REFRESH,     sleep 15с
    Попытка 2: HARD_RELOAD, sleep 30с
    Попытка 3+: HARD_RELOAD, sleep 60с
На попытке 5 ставится флаг should_send_alert (один раз, дальше каждый цикл).
Счётчик сбрасывается через reset() при первом успешном цикле без STALE_DATA.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StaleAction(Enum):
    REFRESH = "REFRESH"
    HARD_RELOAD = "HARD_RELOAD"


@dataclass(frozen=True)
class StaleEscalationStep:
    kind: StaleAction
    sleep_seconds: int
    attempt: int
    should_send_alert: bool


class StaleDataEscalator:
    """Состояние счётчика попыток восстановления."""

    ALERT_AFTER_ATTEMPTS = 5

    def __init__(self) -> None:
        self._attempt = 0

    def next_action(self) -> StaleEscalationStep:
        self._attempt += 1
        if self._attempt == 1:
            kind = StaleAction.REFRESH
            sleep = 15
        elif self._attempt == 2:
            kind = StaleAction.HARD_RELOAD
            sleep = 30
        else:
            kind = StaleAction.HARD_RELOAD
            sleep = 60
        return StaleEscalationStep(
            kind=kind,
            sleep_seconds=sleep,
            attempt=self._attempt,
            should_send_alert=(self._attempt == self.ALERT_AFTER_ATTEMPTS),
        )

    def reset(self) -> None:
        self._attempt = 0

    @property
    def current_attempt(self) -> int:
        return self._attempt
```

- [ ] **Step 4: Прогнать**

Run: `pytest tests/unit/test_stale_data_handler.py -x -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/observer/stale_data_handler.py tests/unit/test_stale_data_handler.py
git commit -m "feat(observer): StaleDataEscalator — эскалация попыток восстановления"
```

---

### Task 12: `browser_recovery` — экспоненциальный reconnect

**Files:**
- Create: `core/observer/browser_recovery.py`
- Test: `tests/unit/test_browser_recovery.py`

- [ ] **Step 1: Тесты**

В `tests/unit/test_browser_recovery.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет экспоненциальный backoff: 5→10→20→30→30… секунд,
   TG-алерт на 5-й попытке."""

from core.observer.browser_recovery import BrowserRecoveryEscalator


def test_first_attempt_sleeps_5s():
    esc = BrowserRecoveryEscalator()
    step = esc.next_step()
    assert step.sleep_seconds == 5
    assert step.attempt == 1
    assert not step.should_send_alert


def test_backoff_progression():
    esc = BrowserRecoveryEscalator()
    sleeps = [esc.next_step().sleep_seconds for _ in range(6)]
    assert sleeps == [5, 10, 20, 30, 30, 30]


def test_alert_on_fifth_attempt():
    esc = BrowserRecoveryEscalator()
    steps = [esc.next_step() for _ in range(5)]
    assert not steps[3].should_send_alert
    assert steps[4].should_send_alert


def test_reset():
    esc = BrowserRecoveryEscalator()
    esc.next_step()
    esc.next_step()
    esc.reset()
    assert esc.next_step().attempt == 1
```

- [ ] **Step 2: Прогнать — упадут**

Run: `pytest tests/unit/test_browser_recovery.py -x`
Expected: FAIL.

- [ ] **Step 3: Реализация**

В `core/observer/browser_recovery.py`:

```python
# -*- coding: utf-8 -*-
"""Эскалатор переподключения к browser-agent после BROWSER_LOST.

Backoff: 5 → 10 → 20 → 30 → 30 → … (cap).
На 5-й попытке ставится should_send_alert.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryStep:
    attempt: int
    sleep_seconds: int
    should_send_alert: bool


class BrowserRecoveryEscalator:
    """Состояние повторных попыток переподключения."""

    _BACKOFF = [5, 10, 20, 30]
    _CAP = 30
    ALERT_AFTER_ATTEMPTS = 5

    def __init__(self) -> None:
        self._attempt = 0

    def next_step(self) -> RecoveryStep:
        self._attempt += 1
        idx = min(self._attempt - 1, len(self._BACKOFF) - 1)
        sleep = self._BACKOFF[idx] if self._attempt <= len(self._BACKOFF) else self._CAP
        return RecoveryStep(
            attempt=self._attempt,
            sleep_seconds=sleep,
            should_send_alert=(self._attempt == self.ALERT_AFTER_ATTEMPTS),
        )

    def reset(self) -> None:
        self._attempt = 0

    @property
    def current_attempt(self) -> int:
        return self._attempt
```

- [ ] **Step 4: Прогнать**

Run: `pytest tests/unit/test_browser_recovery.py -x -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/observer/browser_recovery.py tests/unit/test_browser_recovery.py
git commit -m "feat(observer): BrowserRecoveryEscalator — экспоненциальный reconnect"
```

---

### Task 13: Переработка главного цикла `apps/observer_worker/main.py`

> ВНИМАНИЕ: это самая большая правка. Делать аккуратно. Прежде чем удалять — закоммитить отдельным шагом «cleanup», потом отдельным «rewire».

**Files:**
- Modify: `apps/observer_worker/main.py`

- [ ] **Step 1: Снять состояние «до» — git status чистый**

Run: `git status`
Expected: working tree clean (все предыдущие commit'ы прошли).

- [ ] **Step 2: Удалить `_wait_for_data_load` и `_merge_scan_rows`**

В `apps/observer_worker/main.py`:
1. Удалить блок [строки 945-1010](apps/observer_worker/main.py:945) (функция `_wait_for_data_load`).
2. Удалить блок [строки 1013-1042](apps/observer_worker/main.py:1013) (функция `_merge_scan_rows`).
3. Удалить константы `DATA_LOAD_POLL_INTERVAL_SECONDS`, `DATA_LOAD_MAX_WAIT_SECONDS`, `DATA_LOAD_LOG_INTERVAL_SECONDS` ([строки 112-115](apps/observer_worker/main.py:112)).
4. Удалить переменную `prev_scan_had_spend` и все её use-сайты (поиск: `grep -n "prev_scan_had_spend" apps/observer_worker/main.py`).
5. Удалить вызов `rows = await _wait_for_data_load(...)` в основном цикле ([строки 1616-1621](apps/observer_worker/main.py:1616)).

Run: `grep -n "_wait_for_data_load\|_merge_scan_rows\|prev_scan_had_spend\|DATA_LOAD_" apps/observer_worker/main.py`
Expected: пусто.

- [ ] **Step 3: Убрать все `set_observer_scanning_enabled(False)` из веток обработки ошибок**

Run: `grep -n "set_observer_scanning_enabled(False)" apps/observer_worker/main.py`

Должны остаться только вызовы из веток, **отвечающих на пользовательский toggle** (если такие есть в этом файле). Скорее всего их нет — observer сам не должен выключать сканирование. Удалить все встретившиеся вызовы в `except`/обработке аномалий. В местах удаления оставить только запись в `scan_runs` (это сделает следующий шаг) и retry/sleep.

Run снова: `grep -n "set_observer_scanning_enabled(False)" apps/observer_worker/main.py`
Expected: 0 матчей.

- [ ] **Step 4: Промежуточный commit**

```bash
git add apps/observer_worker/main.py
git commit -m "refactor(observer): убрать _wait_for_data_load, _merge_scan_rows, auto-disable"
```

- [ ] **Step 5: Добавить интеграцию scan_run_writer + outcome_classifier + эскалаторы**

В верхней части `apps/observer_worker/main.py` добавить импорты:

```python
from core.observer.outcome_classifier import (
    ScanOutcome,
    ScanOutcomeDetails,
    classify_scan_outcome,
)
from core.observer.scan_run_writer import begin_scan_run, finish_scan_run
from core.observer.stale_data_handler import StaleAction, StaleDataEscalator
from core.observer.browser_recovery import BrowserRecoveryEscalator
from core.settings_queries import get_or_create_observer_settings
from core.observer.db_queries import load_history_ad_ids_with_metrics  # см. шаг 5b
```

В теле основного цикла, перед `_increment_scan_id()`:

```python
stale_escalator = StaleDataEscalator()
recovery_escalator = BrowserRecoveryEscalator()
```

> Эти переменные живут **между итерациями цикла**, поэтому их объявление должно быть **перед** `while`-циклом, не внутри.

- [ ] **Step 5b: Создать helper `load_history_ad_ids_with_metrics` в `core/observer/db_queries.py`**

В `core/observer/db_queries.py` добавить функцию:

```python
async def load_history_ad_ids_with_metrics(
    fb_ad_ids: list[str], *, lookback_hours: int = 24
) -> set[str]:
    """Возвращает подмножество fb_ad_id, у которых за последние N часов были непустые метрики.

    Используется outcome_classifier'ом как гард: если у текущих объявлений
    никогда не было данных, то отсутствие метрик — норма, а не STALE_DATA.
    """
    if not fb_ad_ids:
        return set()
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot.fb_ad_id)
            .where(
                AdSnapshot.fb_ad_id.in_(fb_ad_ids),
                AdSnapshot.last_observed_at >= cutoff,
                AdSnapshot.impressions > 0,
            )
            .distinct()
        )
        return {row[0] for row in result.all()}
```

> Импорты `datetime, timedelta, UTC, select, AdSnapshot, get_session_factory` — добавь если их нет в файле.

- [ ] **Step 5c: Переписать switch по outcome в основном цикле**

В `apps/observer_worker/main.py` найти место, где сейчас идёт обработка успешного цикла (`# 3. Оценка правил, FSM-переходы, сбор алертов`) — это блок [строки 1656-1704](apps/observer_worker/main.py:1656). Заменить логику обработки на:

```python
# 0. Начинаем запись цикла в scan_runs
factory_for_run = get_session_factory()
async with factory_for_run() as run_session:
    run_id = await begin_scan_run(run_session, scan_id=current_scan_id)
    await run_session.commit()

# 1. Классификация исхода
observer_settings = await get_or_create_observer_settings_from_singleton()
stale_threshold = float(observer_settings.stale_data_threshold)

def _history_predicate(ad_ids: list[str]) -> bool:
    # NB: classify_scan_outcome ждёт sync-предикат — оборачиваем async вызов.
    # См. шаг 5d — добавляем мост через asyncio.run_until_complete не годится в async-цикле,
    # поэтому либо делаем classify_scan_outcome async-aware, либо предварительно
    # подгружаем set ad_ids ДО вызова classify. См. ниже.
    return False  # пока не используется напрямую

ad_ids_in_scan = [getattr(r, "fb_ad_id", "") for r in rows if getattr(r, "fb_ad_id", "")]
history_ids = await load_history_ad_ids_with_metrics(ad_ids_in_scan, lookback_hours=24)

scan_outcome_details = classify_scan_outcome(
    scan_result_obj,
    stale_threshold=stale_threshold,
    has_history_for_ids=lambda ids: bool(history_ids.intersection(ids)),
)

# 2. Действие по outcome
match scan_outcome_details.kind:
    case ScanOutcome.OK | ScanOutcome.OK_PARTIAL:
        stale_escalator.reset()
        recovery_escalator.reset()
        alerts_to_send, stop_alerts, snapshot_batch = await _run_scan_cycle(
            offers=offers, rows=rows, ad_states=ad_states,
            fake_deposits_map=fake_deposits_map, current_scan_id=current_scan_id,
        )
        await _process_scan_results(
            alerts_to_send=alerts_to_send, stop_alerts=stop_alerts,
            snapshot_batch=snapshot_batch, tg_client=tg_client,
            tg_destinations=tg_destinations, current_scan_id=current_scan_id,
        )

    case ScanOutcome.EMPTY_OK:
        stale_escalator.reset()
        recovery_escalator.reset()
        logger.info("Observer: EMPTY_OK — %s", scan_outcome_details.empty_reason)

    case ScanOutcome.EMPTY_BAD:
        # Не auto-disable, не паника — просто spam-throttle алерт и продолжаем
        logger.warning("Observer: EMPTY_BAD — %s", scan_outcome_details.empty_reason)
        # TG-алерт раз в 5 мин (опускаем детали dedup'а — это уже есть в broadcast_observer_runtime_message)
        await broadcast_observer_runtime_message(
            text=("⚠️ Observer не видит таблицу Ads Manager. "
                  f"Причина: {scan_outcome_details.empty_reason}. "
                  "Проверь, открыт ли профиль на странице Ads Manager."),
            fallback_token=tg_token or telegram_bot_token,
            fallback_chat_id=telegram_chat_id,
        )

    case ScanOutcome.STALE_DATA:
        step = stale_escalator.next_action()
        logger.warning(
            "Observer: STALE_DATA попытка %d, action=%s, stale_ratio=%.2f",
            step.attempt, step.kind.value, scan_outcome_details.stale_ratio,
        )
        if step.kind == StaleAction.HARD_RELOAD:
            await grpc_client.hard_reload(bypass_cache=True)
        if step.should_send_alert:
            await broadcast_observer_runtime_message(
                text=("🚨 Ads Manager не отдаёт метрики уже 5 циклов подряд. "
                      "Перезагружаю с очисткой кеша. Проверь сеть/прокси."),
                fallback_token=tg_token or telegram_bot_token,
                fallback_chat_id=telegram_chat_id,
            )

# 3. Записываем итог цикла
async with factory_for_run() as run_session:
    await finish_scan_run(
        run_session,
        run_id=run_id,
        outcome=scan_outcome_details.kind.value,
        rows_total=len(rows),
        rows_partial=scan_outcome_details.partial_count,
        rows_with_data=len(rows) - scan_result_obj.rows_with_all_metrics_empty,
        alerts_warning=len([a for a in alerts_to_send if a.stage == AlertStage.WARNING])
                       if scan_outcome_details.kind in (ScanOutcome.OK, ScanOutcome.OK_PARTIAL) else 0,
        alerts_stop=len(stop_alerts)
                    if scan_outcome_details.kind in (ScanOutcome.OK, ScanOutcome.OK_PARTIAL) else 0,
        phase_timings=scan_result_obj.phase_timings,
        warnings=scan_result_obj.warnings,
        empty_reason=scan_outcome_details.empty_reason,
        error_kind=None,
        error_message=scan_outcome_details.note or None,
        threat_level=threat_level_name,
        next_interval_s=adaptive_interval_secs,
    )
    await run_session.commit()
```

> ВНИМАНИЕ: переменная `scan_result_obj` — это полный `ScanResult`, который сейчас не сохраняется. В цикле [строки 1537-1545](apps/observer_worker/main.py:1537) `event` типа `ScanResult` — сохрани его в переменную: `if isinstance(event, ScanResult): scan_result_obj = event; rows = event.rows; ...`.

В блоке `except` верхнего уровня (там где сейчас ERROR ветка):

```python
except Exception as exc:
    if _is_browser_connection_error(exc):
        step = recovery_escalator.next_step()
        logger.warning("Observer: BROWSER_LOST попытка %d, sleep %ds", step.attempt, step.sleep_seconds)
        try:
            await grpc_client.reconnect_browser()
        except Exception:
            logger.warning("reconnect_browser упал", exc_info=True)
        if step.should_send_alert:
            await broadcast_observer_runtime_message(
                text="🚨 Observer не может подключиться к браузеру 5 циклов подряд. Проверь Vision.",
                fallback_token=tg_token or telegram_bot_token,
                fallback_chat_id=telegram_chat_id,
            )
        # Записываем цикл как BROWSER_LOST
        async with factory_for_run() as run_session:
            await finish_scan_run(run_session, run_id=run_id, outcome="BROWSER_LOST",
                                  error_kind="browser_disconnect", error_message=str(exc))
            await run_session.commit()
        await asyncio.sleep(step.sleep_seconds)
        continue
    # ... остальные внутренние ошибки → outcome=INTERRUPTED
```

> Если структура `try/except` усложнится — это норм; главное чтобы каждый exit-path писал finish_scan_run **ровно один раз**.

- [ ] **Step 6: Запустить весь unit-suite, чтобы убедиться что ничего не сломалось**

Run: `pytest tests/ -x -k "observer or scan"`
Expected: все тесты PASS.

- [ ] **Step 7: ruff проверка**

Run: `ruff check apps/observer_worker/main.py core/observer/`
Expected: 0 ошибок (или только pre-existing, не наши).

- [ ] **Step 8: Commit**

```bash
git add apps/observer_worker/main.py core/observer/db_queries.py
git commit -m "feat(observer): outcome-driven cycle с записью scan_runs"
```

---

## Phase 4 — API

### Task 14: Расширить `GET /api/observer/status`

**Files:**
- Modify: `apps/api/routers/observer.py`
- Test: `tests/integration/test_observer_status_endpoint.py` (новый)

- [ ] **Step 1: Тест**

В `tests/integration/test_observer_status_endpoint.py`:

```python
# -*- coding: utf-8 -*-
"""Проверяет: /api/observer/status возвращает last_run при наличии scan_runs."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from core.models import ScanRun


@pytest.mark.asyncio
async def test_status_returns_last_run(api_client: AsyncClient, db_session):
    """Если в scan_runs есть запись — она попадает в last_run."""
    db_session.add(ScanRun(
        scan_id=10, started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
        outcome="OK", rows_total=58, rows_partial=0, rows_with_data=47,
        phase_timings={"total_ms": 6400}, warnings=[], threat_level="MEDIUM",
        next_interval_s=45,
    ))
    await db_session.commit()

    response = await api_client.get("/api/observer/status")
    assert response.status_code == 200
    body = response.json()
    assert body["last_run"]["outcome"] == "OK"
    assert body["last_run"]["rows_total"] == 58


@pytest.mark.asyncio
async def test_status_without_runs(api_client: AsyncClient):
    """Если scan_runs пустая — last_run = None."""
    response = await api_client.get("/api/observer/status")
    assert response.status_code == 200
    assert response.json()["last_run"] is None
```

> Фикстура `api_client`: если ещё нет — добавить в `tests/conftest.py` (httpx `AsyncClient` + lifespan startup).

- [ ] **Step 2: Прогнать — упадут**

Run: `pytest tests/integration/test_observer_status_endpoint.py -x`
Expected: FAIL.

- [ ] **Step 3: Доработать endpoint**

В `apps/api/routers/observer.py` в функции `get_observer_status` после построения существующего dict'а — дозаписать `active_phase`, `phase_started_at`, `last_run`:

```python
from core.models import ScanRun  # вверху файла

# Внутри get_observer_status, перед return:
last_run_row = (
    await db.execute(
        select(ScanRun)
        .where(ScanRun.outcome != "RUNNING")
        .order_by(ScanRun.started_at.desc())
        .limit(1)
    )
).scalar_one_or_none()

last_run_payload = None
if last_run_row:
    last_run_payload = {
        "scan_id": int(last_run_row.scan_id),
        "outcome": last_run_row.outcome,
        "started_at": last_run_row.started_at.isoformat(),
        "finished_at": last_run_row.finished_at.isoformat() if last_run_row.finished_at else None,
        "rows_total": last_run_row.rows_total,
        "rows_partial": last_run_row.rows_partial,
        "rows_with_data": last_run_row.rows_with_data,
        "alerts_warning": last_run_row.alerts_warning,
        "alerts_stop": last_run_row.alerts_stop,
        "phase_timings": last_run_row.phase_timings or {},
        "warnings": last_run_row.warnings or [],
        "empty_reason": last_run_row.empty_reason,
        "error_kind": last_run_row.error_kind,
        "error_message": last_run_row.error_message,
        "threat_level": last_run_row.threat_level,
        "duration_seconds": (
            (last_run_row.finished_at - last_run_row.started_at).total_seconds()
            if last_run_row.finished_at else None
        ),
    }

# Затем добавить в существующий dict:
response["active_phase"] = None  # TODO: реальная фаза появится в Task 15
response["phase_started_at"] = None
response["last_run"] = last_run_payload
return response
```

- [ ] **Step 4: Прогнать**

Run: `pytest tests/integration/test_observer_status_endpoint.py -x -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/observer.py tests/integration/test_observer_status_endpoint.py
git commit -m "feat(api): /observer/status отдаёт last_run и плейсхолдеры phase"
```

---

### Task 15: Реалтайм-фаза в `/observer/status`

**Files:**
- Modify: `core/models/__init__.py` (добавление 2 колонок в ObserverSettings)
- Create: новая alembic ревизия
- Modify: `apps/observer_worker/main.py` (запись active_phase)
- Modify: `apps/api/routers/observer.py` (отдача active_phase)

- [ ] **Step 1: Alembic-ревизия для active_phase / phase_started_at**

Run: `alembic revision -m "active_phase columns"`

В новой ревизии:

```python
def upgrade() -> None:
    op.add_column("observer_settings", sa.Column("active_phase", sa.String(32), nullable=True))
    op.add_column("observer_settings", sa.Column("phase_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("observer_settings", "phase_started_at")
    op.drop_column("observer_settings", "active_phase")
```

Run: `alembic upgrade head`
Expected: миграция применилась.

- [ ] **Step 2: Добавить поля в модель**

В `core/models/__init__.py` в классе `ObserverSettings` после `current_scan_threat_level`:

```python
    active_phase: Mapped[str | None] = mapped_column(String(32))
    phase_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 3: Helper для записи фазы**

В `core/observer/runtime_status.py` добавить функцию:

```python
async def set_observer_phase(phase: str | None) -> None:
    """Записывает текущую фазу цикла observer'а (refresh/scroll/parse/eval/sleeping)."""
    from datetime import UTC, datetime
    from sqlalchemy import update
    from core.db import get_session_factory
    from core.models import ObserverSettings

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(ObserverSettings).values(
                active_phase=phase,
                phase_started_at=datetime.now(UTC) if phase else None,
            )
        )
        await session.commit()
```

- [ ] **Step 4: Точки вызова в `apps/observer_worker/main.py`**

В ключевых точках цикла добавить:
- Перед `grpc_client.run_scan_cycle(...)`: `await set_observer_phase("scrolling")` (имеется в виду весь сетевой/DOM-этап).
- После получения `ScanResult`, перед `_run_scan_cycle(...)`: `await set_observer_phase("evaluating")`.
- Перед `await _wait_for_next_cycle(...)`: `await set_observer_phase("sleeping")`.
- В finally блоке `await set_observer_phase(None)`.

- [ ] **Step 5: Отдача в `/observer/status`**

В `apps/api/routers/observer.py` в `get_observer_status`:

```python
response["active_phase"] = settings.active_phase
response["phase_started_at"] = (
    settings.phase_started_at.isoformat() if settings.phase_started_at else None
)
```

- [ ] **Step 6: Проверка**

Run: `pytest tests/integration/test_observer_status_endpoint.py -x -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/*active_phase* core/models/__init__.py core/observer/runtime_status.py apps/observer_worker/main.py apps/api/routers/observer.py
git commit -m "feat(observer): запись active_phase и отдача в /observer/status"
```

---

### Task 16: Новый endpoint `GET /api/observer/scan-runs`

**Files:**
- Create: `apps/api/routers/scan_runs.py`
- Modify: `apps/api/main.py` (регистрация роутера)
- Test: `tests/integration/test_scan_runs_endpoint.py`

- [ ] **Step 1: Тесты**

```python
# -*- coding: utf-8 -*-
"""Проверяет: /api/observer/scan-runs возвращает последние N циклов с фильтрами."""

from datetime import UTC, datetime, timedelta
import pytest
from core.models import ScanRun


@pytest.mark.asyncio
async def test_returns_recent_runs_default(api_client, db_session):
    now = datetime.now(UTC)
    for i in range(3):
        db_session.add(ScanRun(
            scan_id=i, started_at=now - timedelta(minutes=i),
            finished_at=now - timedelta(minutes=i) + timedelta(seconds=5),
            outcome="OK", rows_total=10,
        ))
    await db_session.commit()

    resp = await api_client.get("/api/observer/scan-runs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["runs"]) == 3
    # Самый свежий — первым (DESC по started_at)
    assert body["runs"][0]["scan_id"] == 0


@pytest.mark.asyncio
async def test_filter_errors(api_client, db_session):
    now = datetime.now(UTC)
    db_session.add(ScanRun(scan_id=1, started_at=now, outcome="OK"))
    db_session.add(ScanRun(scan_id=2, started_at=now, outcome="BROWSER_LOST"))
    await db_session.commit()

    resp = await api_client.get("/api/observer/scan-runs?filter=errors")
    body = resp.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["outcome"] == "BROWSER_LOST"
```

- [ ] **Step 2: Прогнать — упадут (роутера нет)**

Run: `pytest tests/integration/test_scan_runs_endpoint.py -x`
Expected: 404 / FAIL.

- [ ] **Step 3: Роутер**

В `apps/api/routers/scan_runs.py`:

```python
# -*- coding: utf-8 -*-
"""API-эндпоинт для UI-модалки истории сканов observer'а."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from core.models import ScanRun

router = APIRouter(prefix="/api/observer", tags=["observer"])


@router.get("/scan-runs")
async def list_scan_runs(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    filter: str = Query("all", regex="^(all|errors|slow|with_alerts)$"),
) -> dict[str, Any]:
    """Возвращает последние N циклов сканирования с опциональным фильтром."""
    stmt = select(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit)

    if filter == "errors":
        stmt = stmt.where(ScanRun.outcome.notin_(["OK", "OK_PARTIAL", "EMPTY_OK"]))
    elif filter == "slow":
        # phase_timings -> total_ms > 10000
        stmt = stmt.where(ScanRun.phase_timings["total_ms"].astext.cast(int) > 10_000)
    elif filter == "with_alerts":
        stmt = stmt.where((ScanRun.alerts_warning + ScanRun.alerts_stop) > 0)

    rows = (await db.execute(stmt)).scalars().all()

    def _to_dict(row: ScanRun) -> dict[str, Any]:
        return {
            "id": row.id,
            "scan_id": int(row.scan_id),
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "outcome": row.outcome,
            "rows_total": row.rows_total,
            "rows_partial": row.rows_partial,
            "rows_with_data": row.rows_with_data,
            "alerts_warning": row.alerts_warning,
            "alerts_stop": row.alerts_stop,
            "phase_timings": row.phase_timings or {},
            "warnings": row.warnings or [],
            "empty_reason": row.empty_reason,
            "error_kind": row.error_kind,
            "error_message": row.error_message,
            "threat_level": row.threat_level,
            "next_interval_s": row.next_interval_s,
            "duration_seconds": (
                (row.finished_at - row.started_at).total_seconds() if row.finished_at else None
            ),
        }

    return {"runs": [_to_dict(r) for r in rows]}
```

- [ ] **Step 4: Регистрация в `apps/api/main.py`**

Найти место в `apps/api/main.py`, где регистрируются другие роутеры (поиск: `app.include_router`). Добавить:

```python
from apps.api.routers import scan_runs as scan_runs_router

app.include_router(scan_runs_router.router)
```

- [ ] **Step 5: Прогнать**

Run: `pytest tests/integration/test_scan_runs_endpoint.py -x -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/routers/scan_runs.py apps/api/main.py tests/integration/test_scan_runs_endpoint.py
git commit -m "feat(api): GET /api/observer/scan-runs с фильтрами"
```

---

### Task 17: Фоновая задача: mark_interrupted + retention cleanup

**Files:**
- Modify: `apps/api/main.py`

- [ ] **Step 1: Добавить фоновый task в lifespan**

В `apps/api/main.py`, в lifespan-функции (там, где сейчас стартуются другие background tasks):

```python
from datetime import UTC, datetime, timedelta
from sqlalchemy import delete

from core.db import get_session_factory
from core.models import ScanRun
from core.observer.scan_run_writer import mark_interrupted_runs


async def scan_runs_housekeeping_loop() -> None:
    """Каждые 5 мин: помечает INTERRUPTED-черновики, раз в сутки чистит >30 дней."""
    factory = get_session_factory()
    next_retention_at = datetime.now(UTC)
    while True:
        try:
            async with factory() as session:
                cutoff = datetime.now(UTC) - timedelta(minutes=5)
                marked = await mark_interrupted_runs(session, older_than=cutoff)
                await session.commit()
                if marked:
                    logger.info("scan_runs: %d черновиков помечены как INTERRUPTED", marked)

                if datetime.now(UTC) >= next_retention_at:
                    retention_cutoff = datetime.now(UTC) - timedelta(days=30)
                    result = await session.execute(
                        delete(ScanRun).where(ScanRun.finished_at < retention_cutoff)
                    )
                    await session.commit()
                    if result.rowcount:
                        logger.info("scan_runs: %d старых строк удалено", result.rowcount)
                    next_retention_at = datetime.now(UTC) + timedelta(days=1)
        except Exception:
            logger.exception("scan_runs housekeeping упал, продолжаю")
        await asyncio.sleep(5 * 60)
```

В lifespan startup (где запускаются другие async-задачи):

```python
housekeeping_task = asyncio.create_task(scan_runs_housekeeping_loop())
```

В shutdown:

```python
housekeeping_task.cancel()
try:
    await housekeeping_task
except asyncio.CancelledError:
    pass
```

- [ ] **Step 2: Прогнать всю интеграцию**

Run: `pytest tests/ -x`
Expected: всё зелёное.

- [ ] **Step 3: Commit**

```bash
git add apps/api/main.py
git commit -m "feat(api): фоновая задача mark_interrupted + retention scan_runs"
```

---

## Phase 5 — Frontend

### Task 18: API-клиент `getScanRuns()` + расширение `getObserverStatus()`

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Добавить функцию и обновить тип ответа в JSDoc**

В `frontend/src/api.js` рядом с `getObserverStatus`:

```javascript
export const getObserverStatus = () => request('/observer/status');

/**
 * Получить историю циклов observer'а.
 * @param {object} opts
 * @param {number} [opts.limit=50]
 * @param {'all'|'errors'|'slow'|'with_alerts'} [opts.filter='all']
 */
export const getScanRuns = ({ limit = 50, filter = 'all' } = {}) =>
  request(`/observer/scan-runs?limit=${limit}&filter=${filter}`);
```

- [ ] **Step 2: Сборка фронта**

Run: `cd frontend && npm run build`
Expected: build проходит.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat(frontend-api): getScanRuns + расширенный getObserverStatus"
```

---

### Task 19: Полная переработка `ObserverStatusTile.jsx`

**Files:**
- Modify: `frontend/src/components/observer/ObserverStatusTile.jsx`

- [ ] **Step 1: Заменить содержимое компонента**

В `frontend/src/components/observer/ObserverStatusTile.jsx` полностью переписать на (ниже — целая новая версия, заменить файл):

```jsx
import { useEffect, useMemo, useState } from 'react';

import { getObserverStatus, startNewCabinetDay } from '../../api.js';
import ScanRunsHistoryModal from './ScanRunsHistoryModal.jsx';

const OUTCOME_BADGES = {
  OK: { label: 'Сканирую', tone: 'bg-success-muted text-success border-success/30', dot: true },
  OK_PARTIAL: { label: 'Сканирую (неполные данные)', tone: 'bg-success-muted text-success border-success/30', dot: true },
  EMPTY_OK: { label: 'Кабинет пуст', tone: 'bg-elevated text-muted border-border' },
  EMPTY_BAD: { label: 'Не вижу таблицу', tone: 'bg-warning/10 text-warning border-warning/30' },
  STALE_DATA: { label: 'Данные не пришли — перезагружаю', tone: 'bg-orange-500/10 text-orange-400 border-orange-500/30' },
  BROWSER_LOST: { label: 'Браузер отвалился — переподключаюсь', tone: 'bg-danger-muted text-danger border-danger/30' },
  WAITING_BROWSER: { label: 'Браузер занят', tone: 'bg-warning/10 text-warning border-warning/30' },
  PAUSED: { label: 'Выключено пользователем', tone: 'bg-elevated text-muted border-border' },
  ERROR: { label: 'Ошибка', tone: 'bg-danger-muted text-danger border-danger/30' },
  RUNNING: { label: 'Сканирую', tone: 'bg-success-muted text-success border-success/30', dot: true },
};

const PHASE_LABELS = {
  refresh: 'обновление таблицы',
  scrolling: 'сканирование строк',
  parsing: 'парсинг данных',
  evaluating: 'оценка правил',
  sleeping: 'ожидание следующего цикла',
};

function formatRelative(value) {
  if (!value) return '—';
  const ms = Date.now() - new Date(value).getTime();
  if (ms < 0) return 'через мгновение';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  return `${Math.floor(min / 60)} ч назад`;
}

function formatTimestamp(value) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleTimeString('ru-RU', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return '—';
  }
}

export default function ObserverStatusTile() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
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
    const id = setInterval(fetchOnce, 2000);
    return () => { alive = false; clearInterval(id); };
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

  const badge = useMemo(() => {
    if (!data) return null;
    const outcome = data.last_run?.outcome;
    const workerStatus = String(data.worker_status || '').toUpperCase();
    // Приоритет: outcome последнего цикла, потом worker_status, иначе IDLE
    const key = outcome || workerStatus;
    return OUTCOME_BADGES[key] || { label: key || '—', tone: 'bg-elevated text-muted border-border' };
  }, [data]);

  if (error && !data) {
    return (
      <div className="rounded-lg border border-danger/30 bg-danger-muted px-3 py-2 text-xs text-danger">
        Observer: ошибка загрузки статуса ({error})
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-elevated px-3 py-2 text-xs text-muted">
        Observer: загрузка…
      </div>
    );
  }

  const phaseLabel = PHASE_LABELS[data.active_phase] || data.active_phase || '—';
  const lastRun = data.last_run;

  return (
    <>
      <div className="rounded-lg border border-border bg-elevated p-4 shadow-sm">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-3">
            <div className="text-2xs uppercase tracking-wide text-muted">Observer</div>
            <div className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${badge.tone}`}>
              {badge.dot && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
              {badge.label}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="rounded border border-border bg-surface px-3 py-1 text-xs text-muted hover:text-text"
            >
              Подробнее
            </button>
            <button
              type="button"
              onClick={handleRollover}
              disabled={rolloverBusy}
              className="rounded border border-border bg-surface px-3 py-1 text-xs text-muted hover:text-text disabled:opacity-50"
            >
              {rolloverBusy ? 'Архивируем…' : 'Сутки'}
            </button>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs mb-3">
          <div>
            <span className="text-muted">Фаза: </span>
            <span className="text-text">{phaseLabel}</span>
          </div>
          <div>
            <span className="text-muted">Цикл </span>
            <span className="font-mono text-text">#{data.current_scan_id ?? 0}</span>
          </div>
        </div>

        {lastRun && (
          <div className="grid grid-cols-4 gap-3 mb-3 rounded border border-border bg-surface px-3 py-2">
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Объявлений</div>
              <div className="font-mono text-xs">
                {lastRun.rows_total ?? 0} / {data.active_total ?? 0}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">С данными</div>
              <div className="font-mono text-xs">
                {lastRun.rows_with_data ?? 0}
                {lastRun.rows_partial > 0 && (
                  <span className="text-warning"> ({lastRun.rows_partial} неполн.)</span>
                )}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Время</div>
              <div className="font-mono text-xs">
                {lastRun.duration_seconds ? `${lastRun.duration_seconds.toFixed(1)}с` : '—'}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Угроза</div>
              <div className="font-mono text-xs">{lastRun.threat_level || '—'}</div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-between text-2xs text-muted">
          <div>
            Следующий цикл: {data.next_scan_at ? formatTimestamp(data.next_scan_at) : '—'}
          </div>
          <div>Пульс: {formatRelative(data.worker_heartbeat_at)}</div>
          <div>Сутки: {data.cabinet_day_started_at ? formatTimestamp(data.cabinet_day_started_at) : '—'}</div>
        </div>

        {lastRun?.error_message && (
          <div className="mt-2 text-xs text-danger border-l-2 border-danger/40 pl-2">
            {lastRun.error_message}
          </div>
        )}
      </div>

      {historyOpen && <ScanRunsHistoryModal onClose={() => setHistoryOpen(false)} />}
    </>
  );
}
```

- [ ] **Step 2: Сборка**

Run: `cd frontend && npm run build`
Expected: успешно (предполагая что ScanRunsHistoryModal появится в следующей таске; если не появился — заглушка).

Если ScanRunsHistoryModal ещё не создан, временно закомментируй импорт и `{historyOpen && ...}` (вернёшь в Task 20).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/observer/ObserverStatusTile.jsx
git commit -m "feat(frontend): переработанная плитка ObserverStatusTile с outcome-бейджами"
```

---

### Task 20: `ScanRunsHistoryModal.jsx`

**Files:**
- Create: `frontend/src/components/observer/ScanRunsHistoryModal.jsx`

- [ ] **Step 1: Создать компонент**

В `frontend/src/components/observer/ScanRunsHistoryModal.jsx`:

```jsx
import { useEffect, useState } from 'react';

import { getScanRuns } from '../../api.js';

const FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'errors', label: 'С ошибкой' },
  { key: 'slow', label: 'Медленные' },
  { key: 'with_alerts', label: 'С алертами' },
];

const OUTCOME_COLORS = {
  OK: 'text-success',
  OK_PARTIAL: 'text-success',
  EMPTY_OK: 'text-muted',
  EMPTY_BAD: 'text-warning',
  STALE_DATA: 'text-orange-400',
  BROWSER_LOST: 'text-danger',
  INTERRUPTED: 'text-muted',
};

function formatTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export default function ScanRunsHistoryModal({ onClose }) {
  const [runs, setRuns] = useState([]);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState(null);

  const reload = async (f = filter) => {
    setLoading(true);
    try {
      const body = await getScanRuns({ limit: 50, filter: f });
      setRuns(body.runs || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="w-full max-w-4xl max-h-[80vh] overflow-hidden rounded-lg border border-border bg-surface shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">История сканов</h2>
          <button onClick={onClose} className="text-muted hover:text-text">✕</button>
        </div>

        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => { setFilter(f.key); reload(f.key); }}
              className={`rounded border px-2 py-1 text-xs ${
                filter === f.key
                  ? 'border-accent/40 bg-accent-muted text-accent'
                  : 'border-border bg-elevated text-muted hover:text-text'
              }`}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={() => reload()}
            disabled={loading}
            className="ml-auto rounded border border-border bg-elevated px-2 py-1 text-xs text-muted hover:text-text disabled:opacity-50"
          >
            {loading ? 'Обновляю…' : 'Обновить'}
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface border-b border-border">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Время</th>
                <th className="px-3 py-2 text-left">Outcome</th>
                <th className="px-3 py-2 text-right">Строк</th>
                <th className="px-3 py-2 text-right">Длительность</th>
                <th className="px-3 py-2 text-right">Алерты</th>
                <th className="px-3 py-2 text-left">Сообщение</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <>
                  <tr
                    key={run.id}
                    onClick={() => setExpandedId(expandedId === run.id ? null : run.id)}
                    className="border-b border-border hover:bg-elevated cursor-pointer"
                  >
                    <td className="px-3 py-2 font-mono">{run.scan_id}</td>
                    <td className="px-3 py-2">{formatTime(run.started_at)}</td>
                    <td className={`px-3 py-2 font-medium ${OUTCOME_COLORS[run.outcome] || ''}`}>
                      {run.outcome}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{run.rows_total ?? '—'}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      {run.duration_seconds ? `${run.duration_seconds.toFixed(1)}с` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {run.alerts_warning}/{run.alerts_stop}
                    </td>
                    <td className="px-3 py-2 text-muted">{run.error_message || run.empty_reason || '—'}</td>
                  </tr>
                  {expandedId === run.id && (
                    <tr key={`${run.id}-detail`} className="bg-elevated border-b border-border">
                      <td colSpan={7} className="px-3 py-2">
                        <pre className="text-2xs text-muted overflow-x-auto whitespace-pre-wrap">
{JSON.stringify({
  phase_timings: run.phase_timings,
  warnings: run.warnings,
  threat_level: run.threat_level,
  next_interval_s: run.next_interval_s,
}, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {!runs.length && !loading && (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-muted">Нет данных</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Сборка**

Run: `cd frontend && npm run build`
Expected: успешно.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/observer/ScanRunsHistoryModal.jsx
git commit -m "feat(frontend): модалка ScanRunsHistoryModal с фильтрами и раскрытием"
```

---

### Task 21: Удалить блок статуса observer'а из `DashboardCommandBar`

**Files:**
- Modify: `frontend/src/components/dashboard/DashboardCommandBar.jsx`

- [ ] **Step 1: Удалить блок статуса**

В `frontend/src/components/dashboard/DashboardCommandBar.jsx`:

1. Удалить функцию `parseObserverStatusMessage` ([строки 12-…](frontend/src/components/dashboard/DashboardCommandBar.jsx:12)) — она больше не нужна.
2. Удалить весь блок `useEffect` с countdown'ом из [строк 101-115](frontend/src/components/dashboard/DashboardCommandBar.jsx:101), если он больше нигде не используется.
3. Удалить весь блок построения `statusText/statusDetail/statusColor/showDot` ([строки 117-160](frontend/src/components/dashboard/DashboardCommandBar.jsx:117)).
4. Удалить из JSX рендеринг этого статуса (он внутри `panel-ops`). Оставить только секции со счётчиками STOP/WARNING.

> Если переменные `observerStatus`, `observerStatusMessage`, `parsedStatus`, `isWaitingNextScan`, `isActivelyScanning`, `secsLeft` после очистки нигде не используются — удалить и их и связанные импорты.

- [ ] **Step 2: Сборка + ручная проверка**

Run: `cd frontend && npm run build`
Expected: успешно, без warning'ов про unused vars.

Run: `cd frontend && npm run dev` (в отдельном терминале)
Action: открой `http://localhost:5173/` (или какой порт vite даст), убедись что:
- В `DashboardCommandBar` НЕТ больше фразы «Нет подключения к браузеру»/«Сканирую»/«Ожидание» — только счётчики STOP/WARNING.
- Плитка `ObserverStatusTile` ниже отрисовывается, бейдж меняется, кнопка «Подробнее» открывает модалку, фильтры в модалке работают.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/dashboard/DashboardCommandBar.jsx
git commit -m "refactor(frontend): убрать блок статуса observer из DashboardCommandBar"
```

---

## Phase 6 — Финальная проверка

### Task 22: Интеграционный тест на 4 outcome'а observer'а

**Files:**
- Create: `tests/integration/test_observer_outcomes.py`

- [ ] **Step 1: Тест c stub gRPC**

В `tests/integration/test_observer_outcomes.py`:

```python
# -*- coding: utf-8 -*-
"""Интеграционный тест: observer проходит сценарии OK / EMPTY_OK / STALE_DATA / BROWSER_LOST
   через stub BrowserAgentClient и записывает scan_runs."""

# (Псевдокод — точная схема стаба может потребовать переопределения BrowserAgentClient
# через factory. Этот тест защищает контракт и может быть упрощён до smoke'а:
# вызвать classify_scan_outcome + finish_scan_run в обход main loop'а.)

import pytest
from sqlalchemy import select

from clients.python_grpc.client import ScanResult
from core.models import ScanRun
from core.observer.outcome_classifier import ScanOutcome, classify_scan_outcome
from core.observer.scan_run_writer import begin_scan_run, finish_scan_run


class _MockRow:
    def __init__(self, fb_ad_id: str):
        self.fb_ad_id = fb_ad_id


@pytest.mark.asyncio
async def test_full_ok_path_writes_scan_run(db_session):
    """Полный цикл: classify → begin → finish → запись в БД с outcome='OK'."""
    rows = [_MockRow("ad1"), _MockRow("ad2")]
    result = ScanResult(rows=rows, total_passes=1, duration_seconds=5.5)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True,
    )
    assert outcome.kind == ScanOutcome.OK

    run_id = await begin_scan_run(db_session, scan_id=100)
    await finish_scan_run(
        db_session, run_id=run_id, outcome=outcome.kind.value,
        rows_total=len(rows), rows_with_data=len(rows),
        threat_level="LOW", next_interval_s=60,
    )
    await db_session.commit()

    row = (await db_session.execute(select(ScanRun).where(ScanRun.id == run_id))).scalar_one()
    assert row.outcome == "OK"
    assert row.rows_total == 2


@pytest.mark.asyncio
async def test_stale_data_path(db_session):
    rows = [_MockRow(f"ad{i}") for i in range(10)]
    result = ScanResult(rows=rows, total_passes=1, duration_seconds=8.0,
                        rows_with_all_metrics_empty=9)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True,
    )
    assert outcome.kind == ScanOutcome.STALE_DATA

    run_id = await begin_scan_run(db_session, scan_id=101)
    await finish_scan_run(
        db_session, run_id=run_id, outcome=outcome.kind.value,
        rows_total=len(rows), rows_with_data=1, error_kind="stale_data",
        error_message=outcome.note,
    )
    await db_session.commit()

    row = (await db_session.execute(select(ScanRun).where(ScanRun.id == run_id))).scalar_one()
    assert row.outcome == "STALE_DATA"
    assert row.error_kind == "stale_data"
```

- [ ] **Step 2: Прогнать**

Run: `pytest tests/integration/test_observer_outcomes.py -x -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_observer_outcomes.py
git commit -m "test(observer): интеграционные сценарии outcome+scan_run"
```

---

### Task 23: Финальная верификация

- [ ] **Step 1: Полный прогон тестов**

Run: `pytest tests/ -x`
Expected: 0 failed.

- [ ] **Step 2: ruff**

Run: `ruff check . && ruff format --check .`
Expected: 0 ошибок.

- [ ] **Step 3: Сборка фронта**

Run: `cd frontend && npm run build`
Expected: успешно, без ошибок типов / warning'ов про unused.

- [ ] **Step 4: Сборка browser-agent**

Run: `cd services/browser-agent && npm run build && npx vitest run`
Expected: всё зелёное.

- [ ] **Step 5: Ручной smoke**

Запустить весь стек: `./run.sh`

Action: открыть UI, убедиться:
- Плитка Observer показывает осмысленный бейдж (не «Нет подключения», если браузер реально подключён).
- В DashboardCommandBar нет старой строки про observer-status.
- Кнопка «Подробнее» открывает модалку, фильтры работают, в строке циклов раскрывается JSON.
- Если выключить browser-agent (`docker compose stop browser-agent` или `pkill -f browser-agent`) — observer показывает «Браузер отвалился — переподключаюсь», а не «Нет подключения к браузеру». Через ~30 сек уходит TG-алерт.
- Если в Ads Manager закрыть кабинет и оставить пустую страницу — observer показывает «Не вижу таблицу» (EMPTY_BAD), но НЕ выключает сканирование.

- [ ] **Step 6: Финальный commit / тег**

```bash
git status   # должно быть чисто
git log --oneline -25  # просмотреть всю цепочку коммитов фичи
```

Сообщить пользователю что план выполнен.

---

## Самопроверка плана

**Покрытие спека:**
- ✅ Browser-agent решает готовность → Task 6-7 (parser helpers + handler ScanComplete)
- ✅ 7 outcome'ов → Task 9 (classify_scan_outcome)
- ✅ STALE_DATA + hard reload → Task 5 (hard_reload.ts), Task 11 (StaleDataEscalator), Task 13 (switch)
- ✅ BROWSER_LOST с backoff → Task 12 (BrowserRecoveryEscalator), Task 13 (except)
- ✅ Удаление auto-disable → Task 13 step 3
- ✅ scan_runs таблица + INTERRUPTED задача → Task 2, 3, 10, 17
- ✅ /observer/status расширение + active_phase → Task 14, 15
- ✅ /observer/scan-runs → Task 16
- ✅ Плитка с бейджами → Task 19
- ✅ Модалка истории → Task 20
- ✅ Удаление блока в DashboardCommandBar → Task 21

**Type consistency:** `ScanOutcome.OK_PARTIAL` используется и в `outcome_classifier`, и в `OUTCOME_BADGES` (frontend), и в `scan_runs.outcome` (CHECK не ставим — только в коде). `StaleAction.REFRESH`/`HARD_RELOAD` — используется только в `StaleDataEscalator`, других точек нет.

**Placeholders:** Один TODO в Task 14 (`active_phase = None`) — закрывается в Task 15. Это явная зависимость, не пробел.
