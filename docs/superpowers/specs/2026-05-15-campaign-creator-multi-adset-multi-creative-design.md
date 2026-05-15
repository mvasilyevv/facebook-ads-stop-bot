# Campaign Creator: мульти-adset × мульти-creative через декларативный план

**Дата**: 2026-05-15
**Статус**: Design approved, ready for plan
**Автор**: совместно с пользователем

## Контекст и проблема

Текущий pipeline в `core/campaign_creator/` создаёт строго **один adset с одним ad**. Запись `recordings/20260515_172622_KE_CR2.md` (247 действий, 9 мин 34 сек) показывает реальный production-флоу: **3 adsets × 2 ads = 6 объявлений**, создаваемых через дублирование в FB UI с последующей доводкой (переименование, переприкрепление креативов, обновление tracking URL).

Архитектурные проблемы текущего pipeline:
- Registry с фиксированным `STEPS_ORDER` не поддерживает мультипликацию.
- Параметры шагов берутся из глобального `ctx.task`, что не позволяет иметь несколько инстансов одного шага с разными параметрами.
- Нет понятия «один шаг — одно атомарное действие FB UI».

## Решение: декларативный план шагов

Высокоуровневая спецификация (`spec_json`) разворачивается **PlanBuilder**'ом в линейный список атомарных действий (`plan_json`), который исполняет тонкий **PlanRunner**. Registry становится палитрой атомарных действий — без фиксированного порядка.

```
spec_json  ──PlanBuilder──▶  plan_json  ──PlanRunner──▶  FB UI
(в БД/UI)                    (в БД)                       (Playwright)
```

## Архитектура

Три новых слоя поверх существующего кода:

1. **CampaignSpec** (pydantic) — высокоуровневая декларация в `CampaignCreatorTask.spec_json`.
2. **PlanBuilder** (`core/campaign_creator/plan_builder.py`) — чистая функция `spec → plan`, разворачивает мультипликацию, генерирует имена, добавляет `duplicate_*`/`rename_*`/`reattach_*`/`switch_to_adset`.
3. **PlanRunner** (рефактор `core/campaign_creator/runner.py`) — простой исполнитель: для каждого action из плана берёт step из registry и вызывает `step.execute(ctx, params)`.

Что не меняется: Vision/Playwright stack, recorder, БД-модели (кроме новых полей в `CampaignCreatorTask`), внутренняя логика DOM-взаимодействия в существующих steps.

Ключевое изменение сигнатуры: `BaseStep.execute(ctx, params: dict)` — параметры приходят явно из плана, не из глобального контекста.

## Форматы данных

### `spec_json` — высокоуровневая декларация

```json
{
  "campaign": {
    "objective": "OUTCOME_SALES",
    "name_template": "MV | {geo} | {offer_code} | adset.pro | {date} | {seq}",
    "budget_daily_usd": 50
  },
  "adsets": {
    "count": 3,
    "name_template": "{index}",
    "geo": ["KE"],
    "age_min": 25,
    "age_max": 55,
    "pixel_id": "123...",
    "event": "Purchase",
    "attribution": "7d_click",
    "schedule_start": "2026-05-16T08:00:00+03:00"
  },
  "ads": {
    "per_adset": 2,
    "name_template": "{geo}_{offer_code}_CR{seq:03d}",
    "creatives": ["CR001.mp4", "CR002.mp4"],
    "primary_text": "...",
    "headline": "...",
    "cta": "LEARN_MORE",
    "url_template": "https://...?sub2=MV&sub3={ad_name}&sub4={offer_id}&sub5={campaign_name}&sub6={adset_name}&sub7={ad_name}"
  },
  "offer_id": "KE_CR2",
  "duplication_strategy": "ui_duplicate_then_fix"
}
```

### `plan_json` — раскрытый линейный план

Список объектов вида `{"step": "<name>", "params": {...}}`. Индексы adset/ad стабильны: `adset_index=0` — первый созданный, нумерация ads внутри текущего adset.

Пример полного плана для spec выше — в Appendix A.

### Шаблоны имён

`str.format`-стиль:
- Кампания: `{geo}`, `{offer_code}`, `{date}` (DD.MM), `{seq}`.
- Adset: `{index}` (1-based), `{geo}`, `{offer_code}`.
- Ad: `{geo}`, `{offer_code}`, `{seq:03d}`.
- URL: `{campaign_name}`, `{adset_name}`, `{ad_name}`, `{offer_id}`.

Неизвестный плейсхолдер → `PlanBuildError` на этапе билда.

## Registry: новые шаги

К существующим 16 добавляются 6 атомарных действий:

| Шаг | Параметры | Идемпотентность |
|-----|-----------|-----------------|
| `duplicate_ad` | `source_ad_index` | нет |
| `rename_ad` | `target_ad_index`, `name` | да |
| `reattach_creative` | `ad_index`, `file` | да (по hash/имени файла) |
| `duplicate_adset` | `source_adset_index`, `count` | нет |
| `rename_adset` | `target_adset_index`, `name` | да |
| `switch_to_adset` | `adset_index` | да |

`reattach_creative` — отдельный шаг от `upload_creatives`: после дубликата UI показывает «Creative not available» и требует другой селектор (replace вместо первичной загрузки).

Существующие шаги получают параметры из `params` вместо `ctx.task.*`. Внутренняя DOM-логика не меняется.

## PlanBuilder: алгоритм

Чистая функция `build_plan(spec: CampaignSpec) -> list[PlanAction]`. Без I/O, детерминированная.

```
1. Campaign block
   create_campaign → set_budget → click_next

2. First adset block
   create_adset → set_conversion_location → set_pixel_event
   → set_attribution → set_schedule_start → set_geo → set_age
   → click_next_to_ad

3. First ad inside first adset
   upload_creatives → fill_texts → set_cta → set_tracking_url

4. Remaining ads in first adset (per_adset - 1 раз)
   for j in 1..per_adset-1:
     duplicate_ad(0) → rename_ad(j, name_j)
     → reattach_creative(j, creatives[j]) → set_tracking_url(j, url_j)

5. Duplicate adsets (if count > 1)
   duplicate_adset(0, count-1)
   for i in 1..count-1: rename_adset(i, name_i)

6. Reattach creatives in duplicated adsets
   for i in 1..count-1:
     switch_to_adset(i)
     for j in 0..per_adset-1: reattach_creative(j, creatives[j])

7. save_draft
```

### Валидация spec

- `adsets.count >= 1`, `ads.per_adset >= 1`.
- `len(creatives) >= per_adset`.
- Обязательные поля: `primary_text`, `headline`, `cta`, `pixel_id`, `event`, `offer_id`.
- Невалидный шаблон или плейсхолдер → `PlanBuildError` с русским сообщением.

### Unit-тесты `tests/unit/test_plan_builder.py`

1. spec 1×1 → план идентичен текущему линейному pipeline (regression).
2. spec 3×2 → длина плана соответствует ожидаемой, имена развёрнуты.
3. `per_adset=1` → нет `duplicate_ad` actions.
4. `adsets.count=1` → нет `duplicate_adset`/`switch_to_adset`/`rename_adset`.
5. Невалидный шаблон → `PlanBuildError`.
6. Недостаточно креативов → `PlanBuildError`.

## PlanRunner: контекст исполнения

```python
async def run_plan(
    plan: list[PlanAction],
    page: Page,
    task: CampaignCreatorTask,
    *,
    start_from: int = 0,
    on_progress: Callable[[int, PlanAction, StepResult], Awaitable[None]] | None = None,
) -> RunResult
```

Цикл: для каждого action из `plan[start_from:]` берём step из registry, вызываем `execute(ctx, params)`, обновляем `FBState`, persist progress в БД, дёргаем `on_progress`.

### `StepContext` и `FBState`

```python
@dataclass
class StepContext:
    page: Page
    task: CampaignCreatorTask
    fb_state: FBState

@dataclass
class FBState:
    campaign_id: str | None = None
    adsets: list[AdsetRef] = field(default_factory=list)
    current_adset_index: int = 0

@dataclass
class AdsetRef:
    fb_id: str | None
    name: str
    ads: list[AdRef]

@dataclass
class AdRef:
    fb_id: str | None
    name: str
    creative_file: str | None
```

### Persistence прогресса

Новые поля `CampaignCreatorTask`:
- `spec_json: JSONB` — исходная спека.
- `plan_json: JSONB` — развёрнутый план.
- `progress_index: int default -1` — последний успешно выполненный action.
- `fb_state_json: JSONB` — сериализованный `FBState` для resume.
- `last_error_json: JSONB nullable` — детали последней ошибки.

После каждого успешного шага runner делает короткий UPDATE — это даёт UI live-прогресс и базу для resume.

### Resume

Читаем из БД `plan_json`, `progress_index`, `fb_state_json` → восстанавливаем `FBState` → `run_plan(..., start_from=progress_index + 1)`.

Resume **не повторяет** упавший шаг автоматически. Решение о ретрае/skip принимает пользователь через UI (отдельный спец).

### Обработка ошибок

`BaseStep` оборачивает `execute` в try/except. Playwright-исключения конвертируются в `StepError(step_name, params, screenshot_path, cause)`. Скриншот сохраняется в `recordings/failures/{task_id}_{action_index}.png`.

### Что runner НЕ делает

- Не принимает решений о порядке (это PlanBuilder).
- Не знает о мультипликации.
- Не модифицирует план на лету.
- Не делает retry — это политика наверху.

### Unit-тесты `tests/unit/test_plan_runner.py`

1. Пустой план → no-op.
2. План из 2 действий → каждый `step.execute` вызван по 1 разу с правильными params.
3. Падение на втором действии → `progress_index=0`, raise `PlanRunError(action_index=1)`.
4. Resume с `start_from=2` → первые 2 пропущены.
5. `on_progress` вызывается после каждого успешного шага.

## Миграция БД

Новая Alembic-ревизия `add_plan_to_campaign_creator_task.py`:

```python
op.add_column("campaign_creator_task", sa.Column("spec_json", JSONB, nullable=True))
op.add_column("campaign_creator_task", sa.Column("plan_json", JSONB, nullable=True))
op.add_column("campaign_creator_task", sa.Column("progress_index", sa.Integer, server_default="-1", nullable=False))
op.add_column("campaign_creator_task", sa.Column("fb_state_json", JSONB, nullable=True))
op.add_column("campaign_creator_task", sa.Column("last_error_json", JSONB, nullable=True))
```

Старые поля не трогаем. Backfill не нужен — legacy таски без плана помечаются в UI.

## API изменения

`apps/api/routers/campaign_creator.py`:

- `POST /campaign-creator/tasks` принимает `spec_json` → серверный `PlanBuilder.build_plan(spec)` → сохраняет оба поля. `PlanBuildError` → 422 с детальным русским сообщением.
- `GET /campaign-creator/tasks/{id}` отдаёт `plan_json`, `progress_index`, `last_error_json`.
- `POST /campaign-creator/tasks/{id}/resume` — запускает runner со `start_from=progress_index+1`.
- `GET /campaign-creator/tasks/{id}/plan/preview` — dry-run генерации плана из spec без сохранения, для UI-превью.

## Frontend изменения

`frontend/src/components/CampaignCreatorTimeline.jsx` (уже в git status) — рендер `plan_json` как timeline: карточка на action с именем step и params, подсветка `progress_index` (зелёные/синий/серые), при ошибке — кнопка Resume.

Новая форма `CampaignCreatorSpecForm.jsx` — UI поверх `spec_json`: секции Campaign/Adsets/Ads, поля count/per_adset/шаблоны/тексты/креативы, превью через `/plan/preview`.

## План перехода (поэтапно)

**Этап 1 — фундамент:**
1. Модели (`PlanAction`, `CampaignSpec`, `FBState`).
2. `PlanBuilder` + полные unit-тесты.
3. Миграция БД (новые поля nullable).

**Этап 2 — рефактор steps (по одному):**
4. Поменять сигнатуру `BaseStep.execute(ctx, params)`.
5. Перевести 13 существующих шагов по одному — каждый шаг + тест в одном коммите.
6. Удалить `STEPS_ORDER` из registry.

**Этап 3 — runner:**
7. `PlanRunner` поверх новых steps + тесты.
8. Старый `runner.run()` → `@deprecated`, оставляем рабочим.

**Этап 4 — новые steps мультипликации:**
9. `duplicate_ad`, `rename_ad`, `reattach_creative` + интеграционные тесты.
10. `duplicate_adset`, `rename_adset`, `switch_to_adset` + тесты.

**Этап 5 — API + UI:**
11. Новые эндпоинты, форма, timeline.
12. Dry-run на тестовом FB-аккаунте (с предварительным удалением старой кампании — см. memory `feedback_dry_run_cleanup`).

**Этап 6 — выпил legacy:**
13. После успешного dry-run — удалить старый `runner.run()`.

## Риски и mitigations

- **FB UI меняет селекторы duplicate-кнопок** — recorder-driven селекторы, скриншоты при падении.
- **Индексация после дубликата** — FB может менять порядок в DOM. После `duplicate_*` шаг рескан таблицы и сопоставление **по имени, не по позиции**.
- **Resume может попасть в неконсистентное состояние FB** — перед resume runner валидирует `FBState` против реального DOM (campaign_id, adsets всё ещё существуют).

## Out of scope

- Retry-политика (auto-retry падающих шагов).
- Параллельное создание нескольких кампаний.
- Editing уже созданной черновой кампании через план-diff.
- A/B-тестинг разных шаблонов имён.

## Appendix A — пример полного плана для 3×2

```json
[
  {"step": "create_campaign", "params": {"name": "MV | KE | CR2 | adset.pro | 16.05 | 1", "objective": "OUTCOME_SALES"}},
  {"step": "set_budget", "params": {"amount_usd": 50, "scope": "campaign"}},
  {"step": "click_next", "params": {}},

  {"step": "create_adset", "params": {"name": "1"}},
  {"step": "set_conversion_location", "params": {"value": "website"}},
  {"step": "set_pixel_event", "params": {"pixel_id": "123...", "event": "Purchase"}},
  {"step": "set_attribution", "params": {"value": "7d_click"}},
  {"step": "set_schedule_start", "params": {"iso": "2026-05-16T08:00:00+03:00"}},
  {"step": "set_geo", "params": {"countries": ["KE"]}},
  {"step": "set_age", "params": {"min": 25, "max": 55}},
  {"step": "click_next_to_ad", "params": {}},

  {"step": "upload_creatives", "params": {"files": ["CR001.mp4"], "ad_name": "KE_CR2_CR001"}},
  {"step": "fill_texts", "params": {"primary": "...", "headline": "..."}},
  {"step": "set_cta", "params": {"value": "LEARN_MORE"}},
  {"step": "set_tracking_url", "params": {"url": "https://...?sub3=KE_CR2_CR001&..."}},

  {"step": "duplicate_ad", "params": {"source_ad_index": 0}},
  {"step": "rename_ad", "params": {"target_ad_index": 1, "name": "KE_CR2_CR002"}},
  {"step": "reattach_creative", "params": {"ad_index": 1, "file": "CR002.mp4"}},
  {"step": "set_tracking_url", "params": {"ad_index": 1, "url": "https://...?sub3=KE_CR2_CR002&..."}},

  {"step": "duplicate_adset", "params": {"source_adset_index": 0, "count": 2}},
  {"step": "rename_adset", "params": {"target_adset_index": 1, "name": "2"}},
  {"step": "rename_adset", "params": {"target_adset_index": 2, "name": "3"}},

  {"step": "switch_to_adset", "params": {"adset_index": 1}},
  {"step": "reattach_creative", "params": {"ad_index": 0, "file": "CR001.mp4"}},
  {"step": "reattach_creative", "params": {"ad_index": 1, "file": "CR002.mp4"}},
  {"step": "switch_to_adset", "params": {"adset_index": 2}},
  {"step": "reattach_creative", "params": {"ad_index": 0, "file": "CR001.mp4"}},
  {"step": "reattach_creative", "params": {"ad_index": 1, "file": "CR002.mp4"}},

  {"step": "save_draft", "params": {}}
]
```
