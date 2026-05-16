# Recorder Fix + Analyzer Rewrite — Design

**Дата:** 2026-05-13
**Статус:** утверждён, готов к плану

## Контекст

В проекте уже есть подсистема `core/campaign_recorder/` (CDP-сессия, JS-инжектор, session writer, analyzer) + API `/api/campaign-recorder/*` + UI на `ScriptsPage`. Реализована в фазах 1A/1B/2 ранее.

Все 4 файла в `recordings/` имеют `event_count: 0` — запись не работает. Нет способа понять почему: нет sanity-check инжекта, нет логов состояния, статус сессии не показывает, удалось ли зацепиться за страницу.

Параллельно: текущий `analyzer.py` слишком примитивный (один селектор на событие, без денойза, без группировки) — даже если запись починить, его вывод бесполезен для построения нового шага автоматизации.

## Цель

Сделать инструмент, которым можно пользоваться для каждого нового шага creator'а: нажал «Запись» → сделал действия в Ads Manager → получил **markdown-отчёт** со списком значимых действий, ранжированными селекторами и контекстом → по нему вручную написал шаг в `core/campaign_creator/steps/`.

## Не-цели

- Авто-генерация рабочего Python-кода шага (кодоген) — пропускаем, потому что селекторы FB всё равно требуют ручного выбора и humanizer'а.
- Replay (воспроизведение записи) — не нужно для текущей задачи.
- Миграция/чистка существующих пустых записей — они так и останутся, формат JSON меняем без обратной совместимости.

## Архитектура

Три блока изменений, каждый изолирован:

### Блок 1. Диагностика и починка записи

**Файлы:** `core/campaign_recorder/cdp_session.py`, `event_injector.py`, `apps/api/routers/campaign_recorder.py`, `apps/api/schemas.py`.

**Изменения:**

1. В `cdp_session.connect()` логировать число контекстов и URL всех страниц во всех контекстах при подключении — увидим, не сидит ли Ads Manager в отдельном контексте.
2. В `event_injector.attach_recorder()` после инжекта в каждый фрейм/страницу выполнять sanity-check: `frame.evaluate("() => !!window.__fbRecorder?.installed")`. Возвращать структуру `InjectionReport { pages: [{url, frames_total, frames_injected}] }`.
3. В JS-инжекторе при установке проставить `window.__fbRecorder.session_id = '<uuid>'` (передаётся параметром в `BUILD_JS_INJECTOR(session_id)`). При polling сравнивать — если на фрейме `session_id` другой/отсутствует, переинжектить.
4. В `_run_session` после `attach_recorder` сохранить `injection_report` и `target_url` в `_active_sessions[session_id]`. Если ни одного фрейма не заинжектилось — статус `"error"`.
5. Каждые 10 секунд в цикле логировать `recording session=X pages=N frames=M events_total=K`.
6. Расширить `RecorderStatusResponseSchema` полями: `injection_ok: bool`, `target_url: str | None`, `pages_injected: int`.

**Ключевая гипотеза причины нулей:** `_pick_target_page` берёт первую вкладку, но `attach_recorder(context)` инжектит во все страницы её контекста. Если у Vision второй контекст с реальным Ads Manager — мы инжектим не туда. Логирование контекстов сразу покажет.

### Блок 2. Расширение события

**Файл:** `core/campaign_recorder/event_injector.py` (только JS-часть).

В JS-функцию `record()` добавить поля:

- `label_text: string | null` — текст `<label for=el.id>` или родительского `<label>`.
- `placeholder: string | null` — `el.placeholder` для input/textarea.
- `nearest_heading: string | null` — текст ближайшего `h1..h4` или `[role="heading"]`, идя вверх по DOM не больше 8 уровней и в стороны (предыдущие siblings).
- `selector_candidates: string[]` — массив селекторов в порядке убывания стабильности, посчитанный прямо в JS:
  1. `role=<role>[name="<accessible_name>"]` если есть и role, и доступное имя.
  2. `[aria-label="..."]`.
  3. `[data-testid="..."]` / `[data-pagelet="..."]` / `[data-surface="..."]` (по приоритету).
  4. `text="..."` для button/a/[role=button] с коротким текстом (≤ 60 символов).
  5. CSS-путь по стабильным классам — фильтр: класс отбрасывается если матчится `^x[a-z0-9]{6,}$` (FB-рандом).
  6. xpath (всегда последним).

Поле `selector_candidates` — основа для analyzer.

Старые поля (`xpath`, `id`, `classes`, `data_attrs`, `aria_label`, `role`, `text`, `value`) **сохраняем** — они используются и для будущей отладки полезны.

### Блок 3. Переписать analyzer

**Файлы:** `core/campaign_recorder/analyzer.py` (rewrite), новый `core/campaign_recorder/markdown_report.py`, `apps/api/routers/campaign_recorder.py` (endpoint analyze возвращает путь к md + сам md).

**Pipeline из 3 этапов:**

#### 3.1. Денойз и группировка

Вход — сырые события. Выход — `list[UserAction]` (frozen dataclass):

```python
@dataclass(frozen=True)
class UserAction:
    kind: Literal["click", "fill", "select", "key", "submit"]
    selectors: tuple[str, ...]   # ранжированные кандидаты
    value: str | None            # для fill/select/key
    label: str | None            # label_text или aria_label или text
    section: str | None          # nearest_heading
    ts: float
    raw_indices: tuple[int, ...] # индексы исходных событий, для дебага
```

Правила свёртки:

- `pointerdown` + `mousedown` + `click` на одном элементе (один и тот же xpath) в течение 200мс → один `click`. Берём поля из `click`.
- Подряд идущие `input` события на одном элементе с растущим/меняющимся `value` → один `fill` с финальным `value`. Если последнее событие — `change`, берём его `value`.
- `change` на `<select>` → `select`.
- `keydown` с Enter/Escape/Tab — `key` только если это последнее событие в сессии для данного элемента (явное подтверждение). Иначе отбрасываем.
- Клик по элементу без `selector_candidates` (пустой массив) и без `text` → отбрасываем как шум.

#### 3.2. Markdown-отчёт

Новый модуль `markdown_report.py` с функцией `build_markdown(session: dict, actions: list[UserAction]) -> str`. Формат:

```md
# Запись KE_CR2 — 2026-05-13 14:22 — 7 действий

Источник: `recordings/20260513_142200_KE_CR2.json`
Длительность: 2 мин 14 сек
Сырых событий: 312 → действий: 7

---

## Шаг 1 — click

**Что:** «Conversion Location»
**Секция:** Where do you want to drive traffic?

Селекторы:
1. `role=button[name="Conversion Location"]`
2. `[aria-label="Conversion Location"]`
3. `text="Conversion Location"`

xpath: `/html/body/div[3]/.../div[7]/div`

---

## Шаг 2 — fill

**Что:** поле «Tracking URL»
**Значение:** `https://example.com/landing?fbclid={{fbclid}}`

Селекторы:
1. `[aria-label="Website URL"]`
2. `[placeholder="https://www.example.com/page"]`
```

Сохраняется в `recordings/<timestamp>_<offer>.md` рядом с JSON (та же база имени).

#### 3.3. API + UI

- `GET /api/campaign-recorder/analyze` теперь возвращает `{ json_path, md_path, markdown: str, actions_count, raw_events_count }` вместо текущей структуры. **Breaking change** — никто кроме нашего же UI это не читает.
- `RecorderAnalyzeResponseSchema` обновить.
- На `ScriptsPage` карточка результата:
  - индикатор `injection_ok` во время записи (зелёный/красный + URL цели);
  - кнопка «Открыть отчёт» — раскрывает markdown в `<pre>` или модалке;
  - кнопка «Скачать .md» — отдаёт файл.

### Тесты

- `tests/unit/test_campaign_recorder.py` — обновить под новый формат события (selector_candidates, nearest_heading и т.д.) и под новый analyzer.
- Новые тесты:
  - `test_analyzer_denoise.py` — pointerdown+mousedown+click → один click; серия input → один fill; шум отброшен.
  - `test_markdown_report.py` — генерация md по фикстуре с 3 действиями.
  - `test_injection_report.py` — sanity-check возвращает правильную структуру.
- Все unit без сети, без браузера — Playwright Page мокается.

## Структура изменений

```
core/campaign_recorder/
  cdp_session.py        [edit] логирование контекстов
  event_injector.py     [edit] session_id, sanity-check, расширенные поля события
  session_writer.py     [no change]
  analyzer.py           [rewrite] денойз → UserAction
  markdown_report.py    [new]    генерация md
apps/api/
  routers/campaign_recorder.py  [edit] injection_report, новый /analyze
  schemas.py                    [edit] новые поля статуса и analyze
frontend/src/
  api.js                         [edit] новый ответ /analyze
  pages/ScriptsPage.jsx          [edit] indicator + md viewer
tests/unit/
  test_campaign_recorder.py      [edit]
  test_analyzer_denoise.py       [new]
  test_markdown_report.py        [new]
  test_injection_report.py       [new]
```

## Открытые вопросы

Нет — все решения зафиксированы выше.
