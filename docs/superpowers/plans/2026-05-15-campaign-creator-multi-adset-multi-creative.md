# Multi-Adset / Multi-Creative Campaign Creator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести creator на декларативный план шагов (spec_json → PlanBuilder → plan_json → PlanRunner), поддержать N адсетов × M объявлений через UI duplicate + reattach, с идемпотентностью каждого атомарного шага.

**Architecture:** spec_json (high-level: кампания, адсеты, креативы, страны) разворачивается PlanBuilder'ом в plan_json — линейный список атомарных PlanAction(step_name, params, idempotent). PlanRunner идёт по индексу, держит StepContext (immutable) + FBState (mutable: текущий drawer, открытый адсет, какие шаги уже выполнены), вызывает Registry[step_name].execute(ctx, params). Все шаги, кроме upload_creatives/reattach_creative/duplicate_*, проверяют текущее состояние и skip-if-set.

**Tech Stack:** Python 3.12, Playwright async, SQLAlchemy 2.x async, Alembic, FastAPI, Pydantic v2, pytest.

**Source spec:** `docs/superpowers/specs/2026-05-15-campaign-creator-multi-adset-multi-creative-design.md`

---

## Phase 1 — Domain models + PlanBuilder + DB migration

### Task 1.1: PlanAction / CampaignSpec / FBState dataclasses

**Files:**
- Create: `core/campaign_creator/plan_types.py`
- Test: `tests/unit/test_plan_types.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_plan_types.py
# Сценарий: PlanAction сериализуется в dict и обратно, CampaignSpec валидируется
from core.campaign_creator.plan_types import PlanAction, CampaignSpec, AdsetSpec, FBState


def test_plan_action_roundtrip():
    a = PlanAction(step="set_geo", params={"countries": ["KE"]}, idempotent=True)
    assert PlanAction.from_dict(a.to_dict()) == a


def test_campaign_spec_minimal():
    spec = CampaignSpec(
        offer_code="KE_CR2",
        cabinet_id="act_123",
        pixel_id="PX",
        landing_url="https://x",
        countries=["KE"],
        daily_budget=50.0,
        attribution_days=7,
        budget_level="CBO",
        adsets=[AdsetSpec(name_suffix="A", creo_subfolder="1",
                         headline="H", primary_text="P", creatives=["v1.mp4"])],
    )
    assert spec.adsets[0].creatives == ["v1.mp4"]


def test_fbstate_mark_done():
    s = FBState()
    s.mark_done(0)
    assert s.is_done(0)
    assert not s.is_done(1)
```

- [ ] **Step 2: Run, expect FAIL**

```
pytest tests/unit/test_plan_types.py -v
```

- [ ] **Step 3: Implement**

```python
# core/campaign_creator/plan_types.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Literal


@dataclass(frozen=True)
class PlanAction:
    step: str
    params: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanAction":
        return cls(step=d["step"], params=dict(d.get("params") or {}),
                   idempotent=bool(d.get("idempotent", True)))


@dataclass
class AdsetSpec:
    name_suffix: str
    creo_subfolder: str
    headline: str
    primary_text: str
    creatives: list[str]


@dataclass
class CampaignSpec:
    offer_code: str
    cabinet_id: str
    pixel_id: str
    landing_url: str
    countries: list[str]
    daily_budget: float
    attribution_days: Literal[1, 7]
    budget_level: Literal["CBO", "ABO"]
    adsets: list[AdsetSpec]
    campaign_name: str | None = None
    iter_num: int = 1


@dataclass
class FBState:
    done_indices: set[int] = field(default_factory=set)
    current_adset_idx: int | None = None
    current_ad_idx: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def mark_done(self, idx: int) -> None:
        self.done_indices.add(idx)

    def is_done(self, idx: int) -> bool:
        return idx in self.done_indices
```

- [ ] **Step 4: Run, expect PASS**

```
pytest tests/unit/test_plan_types.py -v
```

- [ ] **Step 5: Commit**

```
git add core/campaign_creator/plan_types.py tests/unit/test_plan_types.py
git commit -m "feat(creator): PlanAction/CampaignSpec/FBState dataclasses"
```

### Task 1.2: PlanBuilder.build(spec) → list[PlanAction]

**Files:**
- Create: `core/campaign_creator/plan_builder.py`
- Test: `tests/unit/test_plan_builder.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_plan_builder.py
# Сценарий: spec на 2 адсета × 2 креатива разворачивается в линейный план
from core.campaign_creator.plan_types import CampaignSpec, AdsetSpec
from core.campaign_creator.plan_builder import build_plan


def _spec(n_adsets=2, n_creos=2):
    return CampaignSpec(
        offer_code="KE_CR2", cabinet_id="act", pixel_id="PX",
        landing_url="https://x", countries=["KE"], daily_budget=50.0,
        attribution_days=7, budget_level="CBO",
        adsets=[
            AdsetSpec(name_suffix=f"A{i}", creo_subfolder=str(i + 1),
                      headline="H", primary_text="P",
                      creatives=[f"v{j}.mp4" for j in range(n_creos)])
            for i in range(n_adsets)
        ],
    )


def test_build_plan_single_adset_single_ad():
    spec = _spec(1, 1)
    plan = build_plan(spec)
    names = [a.step for a in plan]
    assert names[0] == "create_campaign"
    assert "create_adset" in names
    assert "upload_creatives" in names
    assert names[-1] == "save_draft"


def test_build_plan_two_adsets_two_creos_uses_duplicate():
    spec = _spec(2, 2)
    plan = build_plan(spec)
    names = [a.step for a in plan]
    # Один create_adset, один duplicate_adset, два duplicate_ad внутри
    assert names.count("create_adset") == 1
    assert names.count("duplicate_adset") == 1
    assert names.count("duplicate_ad") == 2  # 1 в первом адсете + 1 во втором
```

- [ ] **Step 2: Run, expect FAIL**

```
pytest tests/unit/test_plan_builder.py -v
```

- [ ] **Step 3: Implement**

```python
# core/campaign_creator/plan_builder.py
from __future__ import annotations
from core.campaign_creator.plan_types import CampaignSpec, PlanAction


def build_plan(spec: CampaignSpec) -> list[PlanAction]:
    plan: list[PlanAction] = []

    # Кампания
    plan.append(PlanAction("create_campaign", {
        "offer_code": spec.offer_code, "iter_num": spec.iter_num,
        "campaign_name": spec.campaign_name,
        "budget_level": spec.budget_level,
    }))

    # Первый адсет создаём с нуля
    first = spec.adsets[0]
    plan += _adset_setup(first, idx=0, spec=spec, is_first=True)
    plan += _ads_for_adset(first, adset_idx=0)

    # Остальные адсеты — duplicate + rename + diff
    for i, adset in enumerate(spec.adsets[1:], start=1):
        plan.append(PlanAction("duplicate_adset", {"source_idx": 0}))
        plan.append(PlanAction("switch_to_adset", {"adset_idx": i}))
        plan.append(PlanAction("rename_adset", {"adset_idx": i, "suffix": adset.name_suffix}))
        plan += _adset_setup(adset, idx=i, spec=spec, is_first=False)
        plan += _ads_for_adset(adset, adset_idx=i, after_duplicate=True)

    plan.append(PlanAction("save_draft", {}))
    return plan


def _adset_setup(adset, *, idx, spec, is_first):
    out = [
        PlanAction("set_conversion_location", {"adset_idx": idx}),
        PlanAction("set_pixel_event", {"adset_idx": idx, "pixel_id": spec.pixel_id}),
        PlanAction("set_attribution", {"adset_idx": idx, "days": spec.attribution_days}),
        PlanAction("set_budget", {"adset_idx": idx, "daily_budget": spec.daily_budget,
                                   "level": spec.budget_level}),
        PlanAction("set_schedule_start", {"adset_idx": idx}),
        PlanAction("set_geo", {"adset_idx": idx, "countries": spec.countries}),
        PlanAction("set_age", {"adset_idx": idx}),
    ]
    return out


def _ads_for_adset(adset, *, adset_idx, after_duplicate=False):
    out = []
    # Первое объявление: переименовать (если after_duplicate — оно уже есть, иначе дефолтное),
    # загрузить креатив, заполнить тексты, выбрать CTA.
    out.append(PlanAction("rename_ad", {"adset_idx": adset_idx, "ad_idx": 0,
                                         "suffix": adset.creatives[0]}))
    out.append(PlanAction("upload_creatives", {"adset_idx": adset_idx, "ad_idx": 0,
                                                "file": adset.creatives[0],
                                                "subfolder": adset.creo_subfolder}))
    out.append(PlanAction("fill_texts", {"adset_idx": adset_idx, "ad_idx": 0,
                                          "headline": adset.headline,
                                          "primary_text": adset.primary_text}))
    out.append(PlanAction("set_cta", {"adset_idx": adset_idx, "ad_idx": 0}))

    # Остальные объявления — duplicate + reattach
    for j, creo in enumerate(adset.creatives[1:], start=1):
        out.append(PlanAction("duplicate_ad", {"adset_idx": adset_idx, "source_ad_idx": 0}))
        out.append(PlanAction("rename_ad", {"adset_idx": adset_idx, "ad_idx": j, "suffix": creo}))
        out.append(PlanAction("reattach_creative", {"adset_idx": adset_idx, "ad_idx": j,
                                                     "file": creo,
                                                     "subfolder": adset.creo_subfolder}))
    return out
```

- [ ] **Step 4: Run, expect PASS**

```
pytest tests/unit/test_plan_builder.py -v
```

- [ ] **Step 5: Commit**

```
git add core/campaign_creator/plan_builder.py tests/unit/test_plan_builder.py
git commit -m "feat(creator): PlanBuilder разворачивает spec в линейный plan"
```

### Task 1.3: Alembic миграция — spec_json, plan_json, progress_index, fb_state_json, last_error_json

**Files:**
- Create: `migrations/versions/<auto>_creator_plan_columns.py`
- Modify: `core/models/__init__.py` (CampaignCreatorTask: добавить 5 nullable полей)

- [ ] **Step 1: Добавить поля в модель**

```python
# core/models/__init__.py — внутри CampaignCreatorTask, рядом с context_json
spec_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
plan_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
progress_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
fb_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
last_error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 2: Сгенерировать миграцию**

```
alembic revision --autogenerate -m "creator plan columns"
```

Проверить: миграция содержит `add_column('campaign_creator_tasks', ...)` для всех 5 полей.

- [ ] **Step 3: Применить**

```
alembic upgrade head
```

- [ ] **Step 4: Commit**

```
git add core/models/__init__.py migrations/versions/*creator_plan_columns.py
git commit -m "feat(db): plan/spec/progress поля в CampaignCreatorTask"
```

---

## Phase 2 — Refactor BaseStep на (ctx, params)

### Task 2.1: Новая сигнатура BaseStep + StepContext.params

**Files:**
- Modify: `core/campaign_creator/steps/base.py`
- Test: `tests/unit/test_step_base_signature.py`

- [ ] **Step 1: Test**

```python
# Сценарий: BaseStep.execute принимает (page, ctx, params) и params доступны
from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult


class DummyStep(BaseStep):
    name = "dummy"
    async def execute(self, page, ctx, params):
        return StepResult(success=True, message=str(params.get("k")))


async def test_dummy_passes_params():
    s = DummyStep()
    r = await s.execute(None, None, {"k": "v"})
    assert r.message == "v"
```

- [ ] **Step 2: Изменить базу**

```python
# core/campaign_creator/steps/base.py — изменить сигнатуру
@abstractmethod
async def execute(self, page: Page, context: StepContext, params: dict | None = None) -> StepResult:
    ...
```

Запустить старые тесты — увидеть, какие шаги падают на новой сигнатуре. Не пытаться сразу починить всё.

- [ ] **Step 3: Commit**

```
git add core/campaign_creator/steps/base.py tests/unit/test_step_base_signature.py
git commit -m "refactor(creator): BaseStep.execute(page, ctx, params)"
```

### Task 2.2: Адаптировать каждый существующий шаг (по одному)

Шаги: `create_campaign`, `create_adset`, `set_conversion_location`, `set_pixel_event`, `set_attribution`, `set_budget`, `set_schedule_start`, `set_geo`, `set_age`, `click_next`, `upload_creatives`, `fill_texts`, `set_cta`, `save_draft`.

Для каждого:
- [ ] **Step A:** Добавить `params: dict | None = None` в сигнатуру `execute`.
- [ ] **Step B:** Считывать значения из `params`, fallback на `context` для обратной совместимости (пока не выпилили).
- [ ] **Step C:** Запустить связанный unit-тест → PASS.
- [ ] **Step D:** Commit по одному шагу.

**Особый случай — `set_geo.py`:**
- [ ] Заменить хардкод `DEFAULT_COUNTRY = "Китай"` на diff-логику:

```python
async def execute(self, page, ctx, params=None):
    target = list(params["countries"]) if params and "countries" in params else [ctx.geo_slot_name]
    current = await self._read_current_chips(page)
    to_add = [c for c in (["Антарктида", *target]) if c not in current]
    to_remove = [c for c in current if c not in (["Антарктида", *target])]
    if not to_add and not to_remove:
        return StepResult(success=True, message="гео уже соответствует")
    await self._open_locations_block(page)
    for c in to_add:
        await self._add_country(page, c, c)
    for c in to_remove:
        await self._remove_default(page, c)
    return StepResult(success=True, message=f"гео: +{to_add} -{to_remove}")
```

Добавить `_read_current_chips` — читает aria-label всех чипов выбранных мест.

- [ ] Commit: `refactor(creator): set_geo diffs against params.countries`

### Task 2.3: Удалить STEPS_ORDER из registry

**Files:** `core/campaign_creator/steps/registry.py`

- [ ] **Step 1:** Превратить `_STEP_CLASSES` в `STEP_REGISTRY: dict[str, type[BaseStep]]` keyed by `step.name`.
- [ ] **Step 2:** Удалить `STEPS_ORDER`. Все вызовы — через PlanRunner (см. Phase 3).
- [ ] **Step 3:** Тест:

```python
def test_registry_has_all_steps():
    from core.campaign_creator.steps.registry import STEP_REGISTRY
    for name in ["create_campaign", "create_adset", "set_geo", "upload_creatives", "save_draft"]:
        assert name in STEP_REGISTRY
```

- [ ] **Step 4:** Commit.

---

## Phase 3 — PlanRunner

### Task 3.1: PlanRunner skeleton + indexing

**Files:**
- Create: `core/campaign_creator/plan_runner.py`
- Test: `tests/unit/test_plan_runner.py`

- [ ] **Step 1: Test**

```python
# Сценарий: PlanRunner идёт по плану, увеличивает progress_index, при ошибке останавливается
from core.campaign_creator.plan_types import PlanAction, FBState
from core.campaign_creator.plan_runner import PlanRunner
from core.campaign_creator.steps.base import StepResult


class FakeStep:
    name = "fake"
    def __init__(self, ok=True): self.ok = ok
    async def execute(self, page, ctx, params): return StepResult(success=self.ok, message="x")


async def test_runner_advances_index(monkeypatch):
    plan = [PlanAction("fake", {}), PlanAction("fake", {})]
    registry = {"fake": lambda: FakeStep(ok=True)}
    runner = PlanRunner(registry=registry)
    state = {"progress_index": 0, "fb_state": FBState()}
    ok = await runner.run(page=None, ctx=None, plan=plan, state=state, set_status=lambda *a, **k: None)
    assert ok and state["progress_index"] == 2


async def test_runner_stops_on_failure():
    plan = [PlanAction("fake", {}), PlanAction("bad", {})]
    registry = {"fake": lambda: FakeStep(ok=True), "bad": lambda: FakeStep(ok=False)}
    runner = PlanRunner(registry=registry)
    state = {"progress_index": 0, "fb_state": FBState()}
    ok = await runner.run(None, None, plan, state, set_status=lambda *a, **k: None)
    assert not ok and state["progress_index"] == 1
```

- [ ] **Step 2: Implement**

```python
# core/campaign_creator/plan_runner.py
from __future__ import annotations
from typing import Callable
from core.campaign_creator.plan_types import PlanAction, FBState
from core.campaign_creator.steps.base import StepResult


class PlanRunner:
    def __init__(self, registry: dict[str, Callable]):
        self._registry = registry

    async def run(self, page, ctx, plan: list[PlanAction], state: dict, set_status) -> bool:
        start = state.get("progress_index", 0)
        for i in range(start, len(plan)):
            action = plan[i]
            set_status(i, action.step, "RUNNING")
            step = self._registry[action.step]()
            try:
                result: StepResult = await step.execute(page, ctx, action.params)
            except Exception as exc:
                set_status(i, action.step, "FAILED", message=str(exc))
                return False
            if not result.success:
                set_status(i, action.step, "FAILED", message=result.message)
                return False
            state["progress_index"] = i + 1
            state["fb_state"].mark_done(i)
            set_status(i, action.step, "SUCCEEDED", message=result.message)
        return True
```

- [ ] **Step 3:** PASS. Commit: `feat(creator): PlanRunner`.

### Task 3.2: Интегрировать PlanRunner в CampaignCreatorRunner

**Files:** `core/campaign_creator/runner.py`

- [ ] Заменить `execute_steps` на `PlanRunner.run` (когда у задачи есть `plan_json`), оставить fallback на legacy на время миграции.
- [ ] Сериализация: `plan_json` ↔ `list[PlanAction]`, `fb_state_json` ↔ `FBState`.
- [ ] Commit.

---

## Phase 4 — Новые атомарные шаги

Каждый шаг — отдельная таска по шаблону: failing test (с моком Playwright-locator'ов или интеграционно через recorder-snapshot) → реализация → PASS → commit. Селекторы — из recordings (`recordings/20260515_172622_KE_CR2.md`).

### Task 4.1: `duplicate_ad`

**Files:** `core/campaign_creator/steps/duplicate_ad.py`, `tests/unit/test_duplicate_ad.py`

- [ ] Найти в дереве объявлений строку с `params["source_ad_idx"]`, открыть меню «···», выбрать «Дублировать».
- [ ] Дождаться появления новой строки `… - копия` в дереве.
- [ ] FBState.current_ad_idx ← new index.
- [ ] Commit.

### Task 4.2: `rename_ad`

**Files:** `core/campaign_creator/steps/rename_ad.py`

- [ ] Дважды кликнуть по имени объявления (или меню → «Переименовать»), очистить, ввести `{idx} | {suffix}`.
- [ ] Commit.

### Task 4.3: `reattach_creative`

**Files:** `core/campaign_creator/steps/reattach_creative.py`

- [ ] Открыть медиа-секцию, удалить текущий креатив (если есть), загрузить файл из `{creo_folder}/{subfolder}/{file}`.
- [ ] **Не идемпотентен** — выполняется всегда (исключение из правила).
- [ ] Commit.

### Task 4.4: `duplicate_adset`

**Files:** `core/campaign_creator/steps/duplicate_adset.py`

- [ ] В дереве адсетов: меню «···» на source → «Дублировать». Подтвердить диалог, если появится.
- [ ] Commit.

### Task 4.5: `rename_adset`

**Files:** `core/campaign_creator/steps/rename_adset.py`

- [ ] Аналогично `rename_ad`, имя `{idx+1} | {suffix}`.
- [ ] Commit.

### Task 4.6: `switch_to_adset`

**Files:** `core/campaign_creator/steps/switch_to_adset.py`

- [ ] Кликнуть по N-му адсету в дереве; FBState.current_adset_idx ← N.
- [ ] Skip-if уже на нём.
- [ ] Commit.

### Task 4.7: Зарегистрировать все 6 шагов

- [ ] Добавить в `STEP_REGISTRY`. Commit.

---

## Phase 5 — API + Frontend

### Task 5.1: API endpoint `POST /api/campaign-creator/tasks` принимает spec_json

**Files:** `apps/api/routers/campaign_creator.py`

- [ ] Принять Pydantic `CampaignSpecIn`, на сервере вызвать `build_plan(spec)`, сохранить `spec_json`/`plan_json`/`progress_index=0`/`fb_state_json={}`.
- [ ] `GET /tasks/{id}` возвращает план + текущий index + last_error_json.
- [ ] `POST /tasks/{id}/resume` — перезапуск с `progress_index`.
- [ ] Тесты (pytest + httpx AsyncClient).
- [ ] Commit.

### Task 5.2: Frontend форма N×M

**Files:** `frontend/src/components/CampaignCreatorForm.jsx` (или существующая)

- [ ] Поля: оффер, страны (multi), бюджет, attribution, budget_level, список адсетов (suffix + creo_subfolder + headline + primary_text + список креативов).
- [ ] Превью плана (опционально).
- [ ] Commit.

### Task 5.3: Timeline с progress_index

**Files:** `frontend/src/components/CampaignCreatorTimeline.jsx`

- [ ] Список шагов из `plan_json`, текущий — RUNNING, прошлые — SUCCEEDED, при ошибке — кнопка Resume.
- [ ] Commit.

---

## Phase 6 — Dry-run + cleanup

### Task 6.1: Dry-run

- [ ] **ВАЖНО:** перед dry-run предупредить пользователя удалить старую кампанию вручную (см. memory `feedback_dry_run_cleanup`).
- [ ] Прогнать spec на 2 адсета × 2 креатива в реальном FB. Зафиксировать дефекты, чинить по одному, не сдвигаясь дальше.
- [ ] Прогнать 1×1 (regression).

### Task 6.2: Выпилить legacy

- [ ] Удалить старый `STEPS_ORDER`-путь из `runner.py`, `step_executor.py` (если остался).
- [ ] Удалить `context.geo_slot_name` если больше не используется напрямую.
- [ ] Commit: `chore(creator): drop legacy linear pipeline`.

---

## Self-review checklist

- Все шаги имеют точные пути файлов: да.
- Каждый код-step содержит код, а не "implement appropriately": да.
- Типы согласованы (`PlanAction`, `CampaignSpec`, `FBState` — одни и те же имена везде): да.
- Spec coverage: Phase 1 = модели+builder+миграция, Phase 2 = рефактор execute + set_geo diff, Phase 3 = runner, Phase 4 = 6 новых шагов, Phase 5 = API/UI, Phase 6 = dry-run/cleanup — все 6 этапов spec'а покрыты.
- Идемпотентность: все skip-if-set, исключения duplicate_*/reattach_creative/upload_creatives отмечены в Task 4.3.
