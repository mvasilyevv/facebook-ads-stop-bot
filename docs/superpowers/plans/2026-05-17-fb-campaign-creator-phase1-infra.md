# Phase 1 — Инфраструктура (БД, bridge, TS-каркас)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** Поднять рельсы для будущих фаз: БД-модели `Plan`/`PlanRun`, Alembic-миграция, Python-bridge для инжекта TS-бандла, пустой каркас `services/browser-agent/src/creator/` с `window.__fbAgent` и npm test, который успешно компилирует.

**Architecture:** SQLAlchemy 2.x async + Alembic. Bridge поверх существующего Playwright/CDP-клиента (`core/browser/manager.py`). TS-каркас компилируется в `dist/creator.js` через существующий `tsc` (новых деп не добавляем).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI (для будущего router), TypeScript 5.7, Playwright.

---

## File Structure

- Create: `core/models/__init__.py` — добавить классы `Plan`, `PlanRun` (в существующий файл).
- Create: `core/domain.py` — добавить enum `PlanRunStatus`.
- Create: `migrations/versions/<hash>_add_creator_plan_planrun.py` — Alembic-миграция.
- Create: `core/creator_bridge/__init__.py`
- Create: `core/creator_bridge/bundle.py` — читает `services/browser-agent/dist/creator.js`.
- Create: `core/creator_bridge/runner.py` — инжект через `addInitScript`, биндинг `fbAgentEmit`.
- Create: `services/browser-agent/src/creator/index.ts` — точка входа, экспонирует `window.__fbAgent`.
- Create: `services/browser-agent/src/creator/types.ts` — `Step`, `StepState`, `RecordedEvent`, `PlanContext`.
- Create: `services/browser-agent/src/creator/index.test.ts` — smoke-тест.
- Modify: `services/browser-agent/package.json` — добавить script `build:creator` если требуется.
- Test: `tests/unit/test_creator_models.py`, `tests/unit/test_creator_bridge.py`.

---

### Task 1: Добавить `PlanRunStatus` в `core/domain.py`

- [ ] **Step 1: Failing test**

`tests/unit/test_creator_domain.py`:

```python
# Проверяем что enum PlanRunStatus содержит все требуемые значения.
from core.domain import PlanRunStatus


def test_plan_run_status_values():
    assert {s.value for s in PlanRunStatus} == {
        "queued", "running", "success", "failed", "requires_attention",
    }
```

- [ ] **Step 2: Run** `pytest tests/unit/test_creator_domain.py -x` → FAIL.

- [ ] **Step 3: Implement** — добавить в `core/domain.py`:

```python
class PlanRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    REQUIRES_ATTENTION = "requires_attention"
```

- [ ] **Step 4: Run test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/domain.py tests/unit/test_creator_domain.py
git commit -m "feat(creator): add PlanRunStatus enum"
```

---

### Task 2: ORM-модели `Plan` и `PlanRun`

**Файл:** `core/models/__init__.py` (existing).

- [ ] **Step 1: Failing test** `tests/unit/test_creator_models.py`:

```python
# Проверяем что Plan/PlanRun импортируются и имеют ожидаемые поля.
from core.models import Plan, PlanRun


def test_plan_columns():
    cols = {c.name for c in Plan.__table__.columns}
    assert {"id", "name", "schema_version", "steps", "is_active",
            "created_at", "updated_at"} <= cols


def test_planrun_columns():
    cols = {c.name for c in PlanRun.__table__.columns}
    assert {"id", "plan_id", "profile_id", "variables", "status",
            "started_at", "finished_at", "step_log", "error_message"} <= cols
```

- [ ] **Step 2: Run** → FAIL (нет классов).

- [ ] **Step 3: Implement** — добавить в `core/models/__init__.py`:

```python
from core.domain import PlanRunStatus

_PLAN_RUN_STATUS_ENUM = Enum(
    PlanRunStatus,
    name="plan_run_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Декларативный план создания FB-кампании."""

    __tablename__ = "creator_plans"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PlanRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Запуск плана на конкретном профиле."""

    __tablename__ = "creator_plan_runs"

    plan_id: Mapped[_uuid.UUID] = mapped_column(
        Uuid, ForeignKey("creator_plans.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[PlanRunStatus] = mapped_column(
        _PLAN_RUN_STATUS_ENUM, nullable=False, default=PlanRunStatus.QUEUED
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    step_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Run test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/models/__init__.py tests/unit/test_creator_models.py
git commit -m "feat(creator): add Plan and PlanRun ORM models"
```

---

### Task 3: Alembic-миграция

- [ ] **Step 1: Generate**

```bash
alembic revision --autogenerate -m "add creator_plans and creator_plan_runs"
```

- [ ] **Step 2: Проверить файл** — оставить только создание двух новых таблиц + enum `plan_run_status_enum`. Удалить лишние автогенерируемые правки (drop/alter других таблиц).

- [ ] **Step 3: Apply**

```bash
alembic upgrade head
```

- [ ] **Step 4: Проверить в psql**

```bash
docker compose exec postgres psql -U postgres -d fb_agent -c "\d creator_plans"
docker compose exec postgres psql -U postgres -d fb_agent -c "\d creator_plan_runs"
```

Expected: обе таблицы существуют, FK `plan_id → creator_plans(id) ON DELETE CASCADE`.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/*add_creator_plan*.py
git commit -m "feat(creator): alembic migration for creator_plans/creator_plan_runs"
```

---

### Task 4: TS-каркас `creator/types.ts`

- [ ] **Step 1: Failing test** `services/browser-agent/src/creator/types.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import type { Step, StepState, RecordedEvent, PlanContext } from './types.js';

describe('creator types', () => {
  it('compiles without errors', () => {
    const _state: StepState = { kind: 'unknown' };
    const _ev: RecordedEvent = { type: 'click', selector: '.x', text: '', value: null };
    const _ctx: PlanContext = { variables: {}, emit: () => {} };
    assert.ok(true);
  });
});
```

- [ ] **Step 2: Run** `cd services/browser-agent && npm test` → FAIL (no types.ts).

- [ ] **Step 3: Implement** `services/browser-agent/src/creator/types.ts`:

```typescript
export type StepKind = 'unknown' | 'present' | 'absent' | 'matched' | 'missing';

export interface StepState {
  kind: StepKind;
  current?: unknown;
  meta?: Record<string, unknown>;
}

export interface RecordedEvent {
  type: 'click' | 'input' | 'change';
  selector: string;
  text: string;
  value: string | number | boolean | null;
  reactProps?: Record<string, unknown>;
}

export interface DomState {
  url: string;
  title: string;
}

export interface PlanContext {
  variables: Record<string, unknown>;
  emit(event: string, payload?: unknown): void;
}

export interface Step<I = unknown, O = unknown> {
  name: string;
  match?(ev: RecordedEvent, dom: DomState): boolean;
  detect(ctx: PlanContext): Promise<StepState> | StepState;
  isSatisfied(state: StepState, input: I): boolean;
  execute(state: StepState, input: I, ctx: PlanContext): Promise<O>;
}

export interface PlanStep<I = unknown> {
  step: string;
  input: I;
}

export interface Plan {
  schema_version: number;
  steps: PlanStep[];
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/
git commit -m "feat(creator): TS types for Step/RecordedEvent/PlanContext"
```

---

### Task 5: TS-каркас `creator/index.ts`

- [ ] **Step 1: Failing test** `services/browser-agent/src/creator/index.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';

describe('creator entrypoint', () => {
  it('installs window.__fbAgent', async () => {
    const win: any = {};
    (globalThis as any).window = win;
    await import('./index.js');
    assert.ok(win.__fbAgent, 'window.__fbAgent должен быть установлен');
    assert.equal(typeof win.__fbAgent.run, 'function');
    assert.equal(typeof win.__fbAgent.startRecording, 'function');
    assert.equal(typeof win.__fbAgent.stopRecording, 'function');
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `services/browser-agent/src/creator/index.ts`:

```typescript
import type { Plan, PlanContext } from './types.js';

interface FbAgentApi {
  run(plan: Plan, variables: Record<string, unknown>): Promise<{ ok: boolean; error?: string }>;
  startRecording(planName: string): Promise<void>;
  stopRecording(): Promise<void>;
  version: string;
}

const VERSION = '2.0.0-phase1';

const api: FbAgentApi = {
  version: VERSION,
  async run(_plan, _variables) {
    return { ok: false, error: 'executor not implemented in phase1' };
  },
  async startRecording(_planName) {
    throw new Error('recorder not implemented in phase1');
  },
  async stopRecording() {
    throw new Error('recorder not implemented in phase1');
  },
};

(globalThis as any).window = (globalThis as any).window ?? {};
(globalThis as any).window.__fbAgent = api;

export { api };
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/index.ts services/browser-agent/src/creator/index.test.ts
git commit -m "feat(creator): TS entrypoint with window.__fbAgent stub"
```

---

### Task 6: tsconfig — включить creator в build

- [ ] **Step 1: Inspect** — прочитать `services/browser-agent/tsconfig.json`. Если `include` ограничен, добавить `src/creator/**/*`.

- [ ] **Step 2: Build**

```bash
cd services/browser-agent && npm run build
```

Expected: появляется `dist/creator/index.js`.

- [ ] **Step 3: Commit** (если правился tsconfig)

```bash
git add services/browser-agent/tsconfig.json
git commit -m "build(creator): include creator/ in tsc output"
```

---

### Task 7: Python bundle loader

- [ ] **Step 1: Failing test** `tests/unit/test_creator_bridge.py`:

```python
# Проверяем что bundle.load_bundle читает скомпилированный creator/index.js.
from pathlib import Path

from core.creator_bridge.bundle import load_bundle


def test_load_bundle_returns_nonempty_string(tmp_path: Path):
    fake_dist = tmp_path / "dist" / "creator"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.js").write_text("window.__fbAgent = {};\n", encoding="utf-8")
    code = load_bundle(fake_dist / "index.js")
    assert "window.__fbAgent" in code
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `core/creator_bridge/__init__.py` (пустой) и `core/creator_bridge/bundle.py`:

```python
"""Загрузка скомпилированного TS-бандла creator-агента."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "browser-agent"
    / "dist"
    / "creator"
    / "index.js"
)


def load_bundle(path: Path | None = None) -> str:
    """Читает скомпилированный creator-бандл и возвращает JS-код."""
    target = path or _DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"creator bundle не найден: {target}. Запусти `npm run build` в services/browser-agent."
        )
    return target.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/creator_bridge/
git commit -m "feat(creator): bundle loader (reads dist/creator/index.js)"
```

---

### Task 8: Python runner (inject + binding)

- [ ] **Step 1: Failing test** `tests/unit/test_creator_runner.py`:

```python
# Проверяем что CreatorRunner инжектит бандл через addInitScript и биндит fbAgentEmit.
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.creator_bridge.runner import CreatorRunner


def test_runner_attaches_bundle_and_binding():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()

    runner = CreatorRunner(page, bundle_code="window.__fbAgent={};")
    emitted = []
    asyncio.run(runner.attach(on_emit=lambda ev, payload: emitted.append((ev, payload))))

    page.add_init_script.assert_awaited_once()
    page.expose_binding.assert_awaited_once()
    args, _ = page.expose_binding.call_args
    assert args[0] == "fbAgentEmit"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `core/creator_bridge/runner.py`:

```python
"""Инжект creator-бандла на страницу + биндинг обратной связи."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CreatorRunner:
    """Связывает Playwright Page с TS-бандлом creator-агента."""

    def __init__(self, page: Any, bundle_code: str) -> None:
        self._page = page
        self._bundle_code = bundle_code

    async def attach(self, on_emit: Callable[[str, Any], None]) -> None:
        """Инжектит бандл и регистрирует binding fbAgentEmit."""
        await self._page.add_init_script(self._bundle_code)

        async def _binding(_source: dict[str, Any], event: str, payload: Any = None) -> None:
            on_emit(event, payload)

        await self._page.expose_binding("fbAgentEmit", _binding)

    async def run_plan(self, plan: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        """Вызывает window.__fbAgent.run(plan, variables) на странице."""
        return await self._page.evaluate(
            "([plan, vars]) => window.__fbAgent.run(plan, vars)",
            [plan, variables],
        )
```

- [ ] **Step 4: Run test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/creator_bridge/runner.py tests/unit/test_creator_runner.py
git commit -m "feat(creator): CreatorRunner (addInitScript + expose_binding)"
```

---

### Task 9: End-of-phase smoke

- [ ] **Step 1:** `pytest tests/unit/test_creator_*.py -v` → all PASS.
- [ ] **Step 2:** `cd services/browser-agent && npm run build && npm test` → all PASS.
- [ ] **Step 3:** `ruff check core/creator_bridge core/models core/domain.py` → clean.

---

## Готово к Phase 2 когда

- `Plan` и `PlanRun` существуют в БД через миграцию.
- `CreatorRunner` умеет инжектить бандл и принимать events.
- `dist/creator/index.js` собирается и в браузере выставляет `window.__fbAgent` с заглушками.
- Все юнит-тесты зелёные.
