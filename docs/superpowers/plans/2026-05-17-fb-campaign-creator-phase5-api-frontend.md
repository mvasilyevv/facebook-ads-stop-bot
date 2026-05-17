# Phase 5 — API + Frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** REST API для CRUD планов, запуска PlanRun и отдачи enum/labelMap. Frontend-страница `CreatorPage` со списком планов, формой запуска (динамические селекты из enum'ов) и live-прогрессом PlanRun.

**Architecture:** FastAPI router `apps/api/routers/creator.py` поверх существующего `apps/api/main.py`. React 19 + Vite, страница `frontend/src/pages/CreatorPage.jsx`, polling раз в 2с.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2.x async, React 19, Vite.

---

## File Structure

- Create: `apps/api/routers/creator.py`
- Modify: `apps/api/main.py` — `app.include_router(creator.router)`
- Modify: `apps/api/schemas.py` — `PlanCreate`, `PlanUpdate`, `PlanOut`, `PlanRunOut`, `PlanRunCreate`.
- Create: `core/creator_enums/__init__.py` — реэкспорт всех enum'ов + labelMap'ов из TS в Python (mirror).
- Create: `frontend/src/pages/CreatorPage.jsx`
- Create: `frontend/src/components/PlanRunStatus.jsx`
- Modify: `frontend/src/api.js` — функции creator-эндпоинтов.
- Modify: `frontend/src/App.jsx` — роут `/creator`.
- Test: `tests/unit/test_api_creator.py`, `tests/integration/test_creator_flow.py`.

---

### Task 1: Pydantic-схемы

- [ ] **Step 1: Failing test** `tests/unit/test_creator_schemas.py`:

```python
# Проверяем что PlanCreate валидирует обязательные поля.
import pytest
from pydantic import ValidationError

from apps.api.schemas import PlanCreate


def test_plan_create_requires_name_and_steps():
    with pytest.raises(ValidationError):
        PlanCreate(name="", steps=[])
    p = PlanCreate(name="DRC base", steps=[{"step": "create_campaign", "input": {}}])
    assert p.schema_version == 1
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — добавить в `apps/api/schemas.py`:

```python
class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    schema_version: int = 1
    steps: list[dict[str, Any]]
    is_active: bool = True


class PlanUpdate(BaseModel):
    name: str | None = None
    steps: list[dict[str, Any]] | None = None
    is_active: bool | None = None


class PlanOut(BaseModel):
    id: UUID
    name: str
    schema_version: int
    steps: list[dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlanRunCreate(BaseModel):
    profile_id: str
    variables: dict[str, Any] = Field(default_factory=dict)


class PlanRunOut(BaseModel):
    id: UUID
    plan_id: UUID
    profile_id: str
    variables: dict[str, Any]
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    step_log: list[dict[str, Any]]
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/schemas.py tests/unit/test_creator_schemas.py
git commit -m "feat(creator-api): pydantic schemas for Plan/PlanRun"
```

---

### Task 2: CRUD-эндпоинты для Plan

- [ ] **Step 1: Failing test** `tests/integration/test_creator_api.py`:

```python
# Проверяем CRUD на /plans.
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_list_get_plan(api_client: AsyncClient):
    payload = {"name": "DRC base", "steps": [{"step": "create_campaign", "input": {}}]}
    r = await api_client.post("/plans", json=payload)
    assert r.status_code == 201
    plan_id = r.json()["id"]

    r = await api_client.get("/plans")
    assert any(p["id"] == plan_id for p in r.json())

    r = await api_client.get(f"/plans/{plan_id}")
    assert r.json()["name"] == "DRC base"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `apps/api/routers/creator.py`:

```python
"""API роутер для creator-планов и их запусков."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    PlanCreate,
    PlanOut,
    PlanRunCreate,
    PlanRunOut,
    PlanUpdate,
)
from core.domain import PlanRunStatus
from core.models import Plan, PlanRun

router = APIRouter(tags=["creator"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[Plan]:
    res = await db.execute(select(Plan).order_by(Plan.created_at.desc()))
    return list(res.scalars().all())


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(payload: PlanCreate, db: AsyncSession = Depends(get_db)) -> Plan:
    plan = Plan(**payload.model_dump())
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)) -> Plan:
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plan не найден")
    return plan


@router.put("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: UUID, payload: PlanUpdate, db: AsyncSession = Depends(get_db)
) -> Plan:
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plan не найден")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plan не найден")
    await db.delete(plan)
    await db.commit()
```

- [ ] **Step 4: Wire** — `apps/api/main.py`:

```python
from apps.api.routers import creator

app.include_router(creator.router)
```

- [ ] **Step 5: Run** → PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/routers/creator.py apps/api/main.py tests/integration/test_creator_api.py
git commit -m "feat(creator-api): CRUD endpoints for plans"
```

---

### Task 3: POST /plans/{id}/run — создаёт PlanRun

- [ ] **Step 1: Failing test** в `tests/integration/test_creator_api.py`:

```python
@pytest.mark.asyncio
async def test_run_plan_creates_queued_run(api_client: AsyncClient):
    r = await api_client.post(
        "/plans", json={"name": "p", "steps": [{"step": "create_campaign", "input": {}}]}
    )
    plan_id = r.json()["id"]

    r = await api_client.post(
        f"/plans/{plan_id}/run",
        json={"profile_id": "prof-123", "variables": {"geo": "PL"}},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert body["variables"]["geo"] == "PL"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — добавить в `apps/api/routers/creator.py`:

```python
@router.post(
    "/plans/{plan_id}/run",
    response_model=PlanRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_plan(
    plan_id: UUID, payload: PlanRunCreate, db: AsyncSession = Depends(get_db)
) -> PlanRun:
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plan не найден")
    if not plan.is_active:
        raise HTTPException(400, "plan неактивен")

    run = PlanRun(
        plan_id=plan_id,
        profile_id=payload.profile_id,
        variables=payload.variables,
        status=PlanRunStatus.QUEUED,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


@router.get("/plan-runs/{run_id}", response_model=PlanRunOut)
async def get_plan_run(run_id: UUID, db: AsyncSession = Depends(get_db)) -> PlanRun:
    run = await db.get(PlanRun, run_id)
    if not run:
        raise HTTPException(404, "run не найден")
    return run


@router.get("/plan-runs", response_model=list[PlanRunOut])
async def list_plan_runs(
    plan_id: UUID | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[PlanRun]:
    stmt = select(PlanRun).order_by(PlanRun.created_at.desc()).limit(limit)
    if plan_id:
        stmt = stmt.where(PlanRun.plan_id == plan_id)
    res = await db.execute(stmt)
    return list(res.scalars().all())
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/creator.py tests/integration/test_creator_api.py
git commit -m "feat(creator-api): POST /plans/{id}/run + GET /plan-runs"
```

---

### Task 4: GET /enums/{name} — отдаёт enum + labelMap

- [ ] **Step 1: Mirror enum'ов в Python** — создать `core/creator_enums/__init__.py`:

```python
"""Зеркало TS-enum'ов и labelMap'ов для отдачи во фронт.

Источник истины — `services/browser-agent/src/creator/enums/*`. Здесь храним
только списки значений + RU/EN-лейблы. Если меняешь TS — поправь и тут.
"""

from __future__ import annotations

from typing import TypedDict


class EnumDef(TypedDict):
    values: list[str]
    label_map: dict[str, dict[str, list[str]]]


CONVERSION_LOCATION: EnumDef = {
    "values": ["website", "app", "messenger", "whatsapp", "instagram_direct"],
    "label_map": {
        "website": {"ru": ["Сайт"], "en": ["Website"]},
        "app": {"ru": ["Приложение"], "en": ["App"]},
        "messenger": {"ru": ["Messenger"], "en": ["Messenger"]},
        "whatsapp": {"ru": ["WhatsApp"], "en": ["WhatsApp"]},
        "instagram_direct": {"ru": ["Instagram Direct"], "en": ["Instagram Direct"]},
    },
}

# Аналогично для PIXEL_EVENT, OPTIMIZATION_GOAL, ATTRIBUTION_WINDOW, CTA, OBJECTIVE,
# CURRENCY, PLACEMENT — переносим один-в-один из services/browser-agent/src/creator/enums/.

ALL_ENUMS: dict[str, EnumDef] = {
    "conversion_location": CONVERSION_LOCATION,
    # ...
}
```

- [ ] **Step 2: Failing test**:

```python
@pytest.mark.asyncio
async def test_get_enum_returns_values_and_labels(api_client: AsyncClient):
    r = await api_client.get("/creator/enums/conversion_location")
    assert r.status_code == 200
    body = r.json()
    assert "website" in body["values"]
    assert "Сайт" in body["label_map"]["website"]["ru"]


@pytest.mark.asyncio
async def test_get_enum_404(api_client: AsyncClient):
    r = await api_client.get("/creator/enums/nope")
    assert r.status_code == 404
```

- [ ] **Step 3: Implement** — добавить в `apps/api/routers/creator.py`:

```python
from core.creator_enums import ALL_ENUMS


@router.get("/creator/enums/{name}")
async def get_enum(name: str) -> dict:
    enum_def = ALL_ENUMS.get(name)
    if not enum_def:
        raise HTTPException(404, f"enum '{name}' не найден")
    return enum_def


@router.get("/creator/enums")
async def list_enums() -> list[str]:
    return sorted(ALL_ENUMS.keys())
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/creator_enums apps/api/routers/creator.py tests/integration/test_creator_api.py
git commit -m "feat(creator-api): GET /creator/enums/{name} с labelMap"
```

---

### Task 5: Frontend — API-клиент

- [ ] **Step 1: Implement** — добавить в `frontend/src/api.js`:

```javascript
export const creatorApi = {
  listPlans: () => fetch('/api/plans').then(r => r.json()),
  getPlan: (id) => fetch(`/api/plans/${id}`).then(r => r.json()),
  createPlan: (payload) =>
    fetch('/api/plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => r.json()),
  deletePlan: (id) => fetch(`/api/plans/${id}`, { method: 'DELETE' }),
  runPlan: (id, payload) =>
    fetch(`/api/plans/${id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => r.json()),
  getPlanRun: (id) => fetch(`/api/plan-runs/${id}`).then(r => r.json()),
  listPlanRuns: (planId) =>
    fetch(`/api/plan-runs?plan_id=${planId}`).then(r => r.json()),
  listEnums: () => fetch('/api/creator/enums').then(r => r.json()),
  getEnum: (name) => fetch(`/api/creator/enums/${name}`).then(r => r.json()),
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat(creator-ui): API client functions"
```

---

### Task 6: Компонент PlanRunStatus

- [ ] **Step 1: Implement** `frontend/src/components/PlanRunStatus.jsx`:

```jsx
import { useEffect, useState } from 'react';
import { creatorApi } from '../api';

const STATUS_LABEL = {
  queued: 'В очереди',
  running: 'Выполняется',
  success: 'Готово',
  failed: 'Ошибка',
  requires_attention: 'Требует внимания',
};

export function PlanRunStatus({ runId }) {
  const [run, setRun] = useState(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    const tick = async () => {
      const r = await creatorApi.getPlanRun(runId);
      if (cancelled) return;
      setRun(r);
      if (r.status === 'queued' || r.status === 'running') {
        setTimeout(tick, 2000);
      }
    };

    tick();
    return () => { cancelled = true; };
  }, [runId]);

  if (!run) return <div>Загрузка…</div>;

  return (
    <div className="plan-run-status">
      <h3>{STATUS_LABEL[run.status] ?? run.status}</h3>
      {run.error_message && <pre className="error">{run.error_message}</pre>}
      <ol>
        {run.step_log.map((entry, i) => (
          <li key={i}>
            <strong>{entry.step}</strong>: {entry.event}
            {entry.detail && <span> — {JSON.stringify(entry.detail)}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/PlanRunStatus.jsx
git commit -m "feat(creator-ui): PlanRunStatus с polling"
```

---

### Task 7: CreatorPage — список планов + форма запуска

- [ ] **Step 1: Implement** `frontend/src/pages/CreatorPage.jsx`:

```jsx
import { useEffect, useState } from 'react';
import { creatorApi } from '../api';
import { PlanRunStatus } from '../components/PlanRunStatus';

export function CreatorPage() {
  const [plans, setPlans] = useState([]);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [profileId, setProfileId] = useState('');
  const [variables, setVariables] = useState('{}');
  const [activeRunId, setActiveRunId] = useState(null);

  useEffect(() => {
    creatorApi.listPlans().then(setPlans);
  }, []);

  const handleRun = async () => {
    if (!selectedPlanId || !profileId) return;
    let vars = {};
    try { vars = JSON.parse(variables); } catch { alert('Невалидный JSON'); return; }
    const run = await creatorApi.runPlan(selectedPlanId, {
      profile_id: profileId,
      variables: vars,
    });
    setActiveRunId(run.id);
  };

  return (
    <div className="creator-page">
      <h1>Создание FB-кампаний</h1>

      <section>
        <h2>Планы</h2>
        <ul>
          {plans.map(p => (
            <li
              key={p.id}
              className={p.id === selectedPlanId ? 'selected' : ''}
              onClick={() => setSelectedPlanId(p.id)}
            >
              {p.name} ({p.steps.length} шагов) {p.is_active ? '' : '— неактивен'}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Запуск</h2>
        <label>
          Профиль Vision:
          <input value={profileId} onChange={e => setProfileId(e.target.value)} />
        </label>
        <label>
          Переменные (JSON):
          <textarea
            value={variables}
            onChange={e => setVariables(e.target.value)}
            rows={6}
          />
        </label>
        <button onClick={handleRun} disabled={!selectedPlanId || !profileId}>
          Запустить
        </button>
      </section>

      {activeRunId && (
        <section>
          <h2>Прогресс</h2>
          <PlanRunStatus runId={activeRunId} />
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add route** в `frontend/src/App.jsx`:

```jsx
import { CreatorPage } from './pages/CreatorPage';

// в Routes:
<Route path="/creator" element={<CreatorPage />} />
```

И ссылка в навигации.

- [ ] **Step 3: Smoke build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CreatorPage.jsx frontend/src/App.jsx
git commit -m "feat(creator-ui): CreatorPage со списком планов и формой запуска"
```

---

### Task 8: End-of-phase smoke

- [ ] `pytest tests/unit/test_creator_*.py tests/integration/test_creator_api.py -v` → all PASS.
- [ ] `cd frontend && npm run build` → success.
- [ ] `ruff check apps/api/routers/creator.py core/creator_enums` → clean.

---

## Готово к Phase 6 когда

- API отдаёт CRUD по планам, создаёт PlanRun, отдаёт enum'ы с labelMap.
- `/creator` страница позволяет выбрать план, ввести profile_id + variables JSON, запустить и видеть live-прогресс.
- Worker из Phase 4 подхватывает queued runs и обновляет step_log в БД.
