# План: создание кампаний совпадает с тем, что реально работает

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать четыре отличия нашего создателя кампаний от групп, которые реально крутятся в
кабинетах владельца, и добавить отображаемую ссылку.

**Architecture:** Один вертикальный slice создания: контракт черновика
(`core/campaign_drafts/contracts.py`), конфиг сборки (`core/campaign_builder/config.py`), тело
запроса к Meta (`core/campaign_builder/builder.py`), схема пресета и оба фронта. Ничего нового
не изобретаем — приводим то, что шлём, к тому, что снято с 360 живых групп.

**Tech Stack:** Python 3.11 + pydantic + SQLAlchemy Core, FastAPI, React + Tailwind,
pytest, vitest.

## Global Constraints

- Код, тесты и комментарии — по-русски; имена типов, API-полей и технических
  идентификаторов остаются английскими.
- Money-путь: сначала инвариант и regression test, потом правка.
- Один архитектурный слой или один вертикальный slice за PR.
- `pytest` только на изолированной БД; на боевой `:5433` он сносит `offers`/`offer_rules`.
  Локальный прогон — `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest`.
- Ничего не создавать и не менять в боевых кабинетах.
- Сверять OpenAPI, generated clients и оба frontend, если менялся контракт.
- Прод выкатывается только `gh workflow run Release --ref main -f bootstrap=false`;
  push в main ничего не выкатывает.
- `special_ad_categories` остаётся `NONE` — решение владельца 17.08, пересмотру не подлежит.

## Основание: замер 17.08 по живым кабинетам

Read-only Graph API через CDP боевой Vision-сессии. **55 кампаний, 360 групп** в кабинетах
`1855748448431929`, `1386439193678186`, `996458953428822`. Полный отчёт —
`docs/meta-api-coverage-2026-08.md`.

Шесть из семи зашитых значений **совпали с практикой** и правки не требуют: `OUTCOME_SALES`
(55/55), `AUCTION` (55/55), пустая спец-категория (55/55), `OFFSITE_CONVERSIONS` (360/360),
`IMPRESSIONS` (360/360). Прежняя редакция этого плана считала их замками по перечню SDK — это
было неверно, перечень возможного оказался почти целиком шумом.

Реальные отличия, по убыванию охвата:

| Что стоит в живых группах | Охват | У нас |
|---|---|---|
| `targeting_optimization: "expansion_all"` | 360 из 360 | не шлём |
| `targeting_automation.individual_setting` | есть в примерах | шлём только `advantage_audience` |
| `age_range: [min, max]` | 351 из 360 | не шлём |
| `brand_safety_content_filter_levels` | 345 из 360 | не шлём |
| `bid_strategy: LOWEST_COST_WITHOUT_CAP` | 41 из 55 кампаний | не умеем |
| `publisher_platforms` | **0 из 360** | шлём, если оператор выбрал площадки |

Ни одна из 360 групп не использует `custom_audiences`, `interests`, `flexible_spec`, `cities`,
`regions`, `locales`, `device_platforms` — поддержку этих 96 ключей не планируем.

Эталон живой группы, к которому приводим (кабинет `1855748448431929`):

```json
{
 "age_min": 25, "age_max": 65, "age_range": [25, 55], "genders": [1],
 "geo_locations": { "countries": ["AQ", "NG"], "location_types": ["home", "recent"] },
 "targeting_optimization": "expansion_all",
 "brand_safety_content_filter_levels": ["FACEBOOK_RELAXED", "AN_RELAXED"],
 "targeting_automation": { "advantage_audience": 1, "individual_setting": { "age": 1, "gender": 1 } }
}
```

## Границы плана

Этот план — только создание кампаний. Видео-метрики (hook/hold rate, профиль досмотра, сводка
по креативам и выводы ассистента) и перенос групп между кабинетами вынесены в отдельные планы:
это независимые подсистемы со своим циклом проверки, и смешивать их в одном PR нельзя.
Разведка для них уже сделана и лежит в `docs/meta-api-coverage-2026-08.md` и в истории этого
файла.

Способ чтения боевых данных, которым снят замер, воспроизводим: Vision отдаёт CDP-порт профиля
по `GET http://127.0.0.1:3030/list` внутри контейнера стола; browser-agent делит с ним сетевой
namespace, у него есть `playwright-core`; `chromium.connectOverCDP` → вкладка Ads Manager →
`access_token` из `performance.getEntriesByType('resource')` (брать URL, где есть `/vNN.N/`,
иначе версия определится неверно) → `fetch` к `adsmanager-graph.facebook.com` изнутри страницы.

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `core/campaign_builder/config.py` | Модель `Targeting`, набор стратегий, `display_link` | A1, A2, A3 |
| `core/campaign_builder/builder.py` | Тело группы и креатива для Meta | A1, A3 |
| `core/campaign_drafts/contracts.py` | Поля черновика: стратегия, ссылка | A2, A3 |
| `apps/api/routers/v1/schemas/campaigns_create.py` | Схема пресета | A4 |

| `frontend/`, `frontend-mini/` | Выбор стратегии, поле ссылки, честная подпись площадок | A2, A3 |

---

### Task A1: Группа объявлений совпадает с рабочим шаблоном кабинета

Самое дорогое отличие: наши группы уходят в Meta с другим таргетингом, чем те 360, что реально
крутятся. Это money-путь — таргетинг определяет, кому и почём показывается реклама.

**Files:**
- Modify: `core/campaign_builder/config.py:148-174` (модель `Targeting`)
- Modify: `core/campaign_builder/builder.py` (сборка `body["targeting"]`)
- Test: `tests/unit/test_campaign_builder.py`

**Interfaces:**
- Produces: `Targeting.targeting_optimization: str = "expansion_all"`,
  `Targeting.brand_safety_relaxed: bool = True`,
  `Targeting.age_range_min: int | None = None`. Билдер кладёт их в
  `targeting.targeting_optimization`, `targeting.brand_safety_content_filter_levels`,
  `targeting.age_range` и расширяет `targeting_automation.individual_setting`.

- [ ] **Step 1: Написать падающий тест**

Добавь в `tests/unit/test_campaign_builder.py`. Конструктор `CampaignConfig` бери у ближайшего
существующего теста этого файла — не выдумывай сигнатуру.

```python
def test_adset_targeting_matches_the_cabinet_template() -> None:
    """Наши группы уходили в Meta с другим таргетингом, чем 360 живых.

    Замер 17.08: `targeting_optimization` стоит в 360 группах из 360,
    `brand_safety_content_filter_levels` — в 345, `age_range` — в 351.
    Мы не слали ни одного из них, и это не косметика: таргетинг решает,
    кому и почём показывается реклама.
    """
    cfg = _config(countries=["NG"], age_min=25, age_max=65)

    targeting = adset_body(cfg, name="test")["targeting"]

    assert targeting["targeting_optimization"] == "expansion_all"
    assert targeting["brand_safety_content_filter_levels"] == ["FACEBOOK_RELAXED", "AN_RELAXED"]
    assert targeting["age_range"] == [25, 65]
    assert targeting["targeting_automation"]["individual_setting"] == {"age": 1, "gender": 1}


def test_placements_stay_absent_when_operator_picked_none() -> None:
    """`publisher_platforms` не встречается ни в одной из 360 живых групп.

    Пустой список обязан означать «поле не слать»: любой выбор площадок
    уводит группу от рабочего шаблона, где площадки отдаются автоматике.
    """
    cfg = _config(countries=["NG"])

    assert "publisher_platforms" not in adset_body(cfg, name="test")["targeting"]
```

Имя функции сборки группы возьми фактическое:
`grep -n "def .*adset.*body\|def adset_body" core/campaign_builder/builder.py`.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q -k template`
Expected: FAIL — `KeyError: 'targeting_optimization'`.

- [ ] **Step 3: Расширить модель таргетинга**

В `core/campaign_builder/config.py`, в классе `Targeting` после `advantage_audience`:

```python
    # Значения сняты с 360 живых групп кабинетов 17.08: без них наша группа
    # уходит в Meta не такой, как те, что реально откручиваются.
    # expansion_all — расширение аудитории за пределы заданной, стоит в 360 из 360.
    targeting_optimization: str = "expansion_all"
    # FACEBOOK_RELAXED/AN_RELAXED — минимальная фильтрация контента, в 345 из 360.
    brand_safety_relaxed: bool = True
```

- [ ] **Step 4: Собирать тело группы по эталону**

В `core/campaign_builder/builder.py`, в словаре `body["targeting"]`, после `age_max` добавь:

```python
            # age_range дублирует age_min/age_max отдельным полем: так его шлёт
            # сам Ads Manager (351 группа из 360), и Meta ожидает оба.
            "age_range": [cfg.targeting.age_min, cfg.targeting.age_max],
            "targeting_optimization": cfg.targeting.targeting_optimization,
```

Там же, `targeting_automation` замени целиком на:

```python
            "targeting_automation": {
                "advantage_audience": 1 if cfg.targeting.advantage_audience else 0,
                # Подфлаги возраста и пола идут вместе с advantage_audience во всех
                # живых группах: без них Advantage+ не расширяет по этим осям.
                "individual_setting": {"age": 1, "gender": 1},
            },
```

И после блока с `genders` добавь:

```python
    if cfg.targeting.brand_safety_relaxed:
        body["targeting"]["brand_safety_content_filter_levels"] = [
            "FACEBOOK_RELAXED",
            "AN_RELAXED",
        ]
```

- [ ] **Step 5: Прогнать тесты билдера**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q`
Expected: PASS. Если упали соседние тесты, сверяющие тело группы целиком — обнови в них
ожидаемый словарь, это и есть смысл задачи.

- [ ] **Step 6: Коммит**

```bash
git add core/campaign_builder/config.py core/campaign_builder/builder.py tests/unit/test_campaign_builder.py
git commit -m "fix(campaigns): таргетинг группы совпадает с рабочим шаблоном кабинета"
```

---

### Task A2: Стратегия ставок перестаёт быть одной

41 живая кампания из 55 идёт на `LOWEST_COST_WITHOUT_CAP` — три четверти того, что крутится,
наш билдер выпустить не может.

**Files:**
- Modify: `core/campaign_drafts/contracts.py:64`
- Modify: `core/campaign_builder/config.py:100,125`
- Modify: `frontend/`, `frontend-mini/` (выбор стратегии)
- Test: `tests/unit/test_campaign_drafts.py`

**Interfaces:**
- Produces: `BidStrategy = Literal["COST_CAP", "LOWEST_COST_WITHOUT_CAP",
  "LOWEST_COST_WITH_BID_CAP", "LOWEST_COST_WITH_MIN_ROAS"]` в
  `core/campaign_drafts/contracts.py` — импортируется схемой пресета в A4.

- [ ] **Step 1: Написать падающий тест**

Добавь в `tests/unit/test_campaign_drafts.py`:

```python
def test_draft_allows_the_strategy_three_quarters_of_the_cabinet_runs() -> None:
    """41 живая кампания из 55 идёт на «Максимальное количество».

    Замок на COST_CAP означал, что наш создатель не воспроизводит три
    четверти того, что в кабинетах уже работает.
    """
    goal = CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP")

    assert goal.bid_strategy == "LOWEST_COST_WITHOUT_CAP"


def test_uncapped_strategy_does_not_demand_a_bid() -> None:
    """`bid_amount` — поле кэпа. Требовать его у стратегии без кэпа значит
    закрыть её замком, который сам же и придумал."""
    goal = CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP", bid_amount="")

    assert goal.bid_amount == ""
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py -q -k strategy`
Expected: FAIL — `Literal["COST_CAP"]` не принимает другое значение.

- [ ] **Step 3: Расширить контракт**

В `core/campaign_drafts/contracts.py` перед `class CampaignDraftGoal`:

```python
# Четыре стратегии Meta. Список расширяется правкой кода осознанно: справочник
# у Meta мы не читаем, и молча появиться тут ничего не должно.
BidStrategy = Literal[
    "COST_CAP",
    "LOWEST_COST_WITHOUT_CAP",
    "LOWEST_COST_WITH_BID_CAP",
    "LOWEST_COST_WITH_MIN_ROAS",
]
```

И в `CampaignDraftGoal` замени `bid_strategy: Literal["COST_CAP"] = "COST_CAP"` на:

```python
    bid_strategy: BidStrategy = "COST_CAP"
```

- [ ] **Step 4: Проверить, что ставка не требуется без кэпа**

Run: `grep -n "_CAPPED_BID_STRATEGIES" core/campaign_builder/config.py`
Expected: набор содержит `COST_CAP` и `LOWEST_COST_WITH_BID_CAP`. Если там есть
`LOWEST_COST_WITHOUT_CAP` — убери: стратегия без кэпа не должна требовать `bid_amount`.

- [ ] **Step 5: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py -q`
Expected: PASS.

- [ ] **Step 6: Дать выбор в обоих фронтах**

Найди зашитое значение: `grep -rn "COST_CAP" frontend/src frontend-mini/src packages/shared/src`

Замени на селект с подписями Ads Manager: `LOWEST_COST_WITHOUT_CAP` → «Максимальное
количество», `COST_CAP` → «Цель по цене за результат», `LOWEST_COST_WITH_BID_CAP` →
«Предельная ставка», `LOWEST_COST_WITH_MIN_ROAS` → «Цель по ROAS». Поле ставки показывается
только для стратегий из `_CAPPED_BID_STRATEGIES`.

- [ ] **Step 7: Гейты и коммит**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
git add -A && git commit -m "feat(campaigns): выбор стратегии ставок вместо единственной COST_CAP"
```

---

### Task A3: Отображаемая ссылка

Названа владельцем прямо: `play.ghana.com` вместо сырого домена трекера. Поле `caption` в
`link_data`; дока Meta: «overwrites the caption under the title in the link», значение обязано
быть настоящим URL, отражающим домен назначения.

**Files:**
- Modify: `core/campaign_drafts/contracts.py` (в `CampaignDraftGoal` после `cta`)
- Modify: `core/campaign_builder/config.py` (в `CampaignConfig` рядом с `cta`)
- Modify: `core/campaign_builder/builder.py` (функция `_link_data`)
- Test: `tests/unit/test_campaign_builder.py`, `tests/unit/test_campaign_drafts.py`

**Interfaces:**
- Produces: `CampaignConfig.display_link: str` и `CampaignDraftGoal.display_link: str`.

- [ ] **Step 1: Написать падающие тесты билдера**

```python
def test_display_link_reaches_meta_as_caption() -> None:
    """Отображаемая ссылка — это `caption` в link_data, а не отдельная сущность."""
    cfg = _config(destination_link="https://trk.example.com/click?c=1", display_link="play.ghana.com")

    link_data = _link_data(cfg, {"image_hash": "abc"})

    assert link_data["caption"] == "play.ghana.com"
    assert link_data["link"] == "https://trk.example.com/click?c=1"


def test_display_link_is_omitted_when_not_set() -> None:
    """Пустой caption Meta трактует как ошибку, а не как «показать домен»."""
    cfg = _config(destination_link="https://trk.example.com/click?c=1")

    assert "caption" not in _link_data(cfg, {"image_hash": "abc"})
```

- [ ] **Step 2: Убедиться, что падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q -k display_link`
Expected: FAIL — `CampaignConfig` не принимает `display_link`.

- [ ] **Step 3: Добавить поле в конфиг и в тело креатива**

В `core/campaign_builder/config.py`, в `CampaignConfig` рядом с `cta`:

```python
    # Отображаемая ссылка под заголовком (link_data.caption). Meta требует
    # настоящий URL, отражающий домен назначения. Пустая строка = поле не слать:
    # пустой caption трактуется как ошибка.
    display_link: str = ""
```

В `core/campaign_builder/builder.py`, в `_link_data` сразу после `ld.update(media)`:

```python
    if cfg.display_link:
        ld["caption"] = cfg.display_link
```

- [ ] **Step 4: Прогнать**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Написать падающий тест контракта**

```python
import pytest

from core.campaign_drafts.contracts import CampaignDraftGoal


def test_draft_keeps_display_link() -> None:
    assert CampaignDraftGoal(display_link="play.ghana.com").display_link == "play.ghana.com"


@pytest.mark.parametrize("value", ["играй тут", "play ghana", "http://", "ghana"])
def test_draft_rejects_a_display_link_that_is_not_a_url(value: str) -> None:
    """Meta отклонит такой креатив уже на создании — ловим раньше неё, иначе
    владелец узнаёт об ошибке из невнятного ответа посреди залива."""
    with pytest.raises(ValueError):
        CampaignDraftGoal(display_link=value)
```

- [ ] **Step 6: Добавить поле в контракт черновика**

В `core/campaign_drafts/contracts.py`, в `CampaignDraftGoal` после `cta`:

```python
    display_link: str = Field(default="", max_length=255)
```

И валидатор в том же классе (модуль `re` уже импортирован, строка 9):

```python
    @field_validator("display_link")
    @classmethod
    def validate_display_link(cls, value: str) -> str:
        """Meta принимает в caption только настоящий URL или домен."""
        if not value:
            return value
        if re.fullmatch(r"(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s]*)?", value) is None:
            raise ValueError("display_link must be a URL or a domain")
        return value
```

- [ ] **Step 7: Пробросить в API и оба фронта**

Найди, где проброшен `destination_link`
(`grep -rn "destination_link" apps/api frontend/src frontend-mini/src`), и добавь
`display_link` тем же способом. В форме — под ссылкой назначения, подпись «Отображаемая
ссылка», подсказка «Например, play.ghana.com».

- [ ] **Step 8: Гейты и коммит**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
git add -A && git commit -m "feat(campaigns): отображаемая ссылка в креативе"
```

---

### Task A4: Пресет перестаёт быть неполным

Пресет не хранит `bid_amount`, хотя `COST_CAP` без него не собирается
(`core/campaign_builder/config.py:125`). После загрузки заготовки ставку всё равно вводят руками.

**Files:**
- Modify: `apps/api/routers/v1/schemas/campaigns_create.py:285-306`
- Test: `tests/unit/test_campaigns_create_schemas.py`

**Interfaces:**
- Consumes: `BidStrategy` из A2, `display_link` из A3.
- Produces: `PresetIn.bid_strategy`, `PresetIn.bid_amount`, `PresetIn.display_link`.

- [ ] **Step 1: Написать падающий тест**

Создай `tests/unit/test_campaigns_create_schemas.py`:

```python
from apps.api.routers.v1.schemas.campaigns_create import PresetIn


def _preset(**overrides):
    values = {"name": "GH Aviator", "countries": ["GH"], "daily_budget": "8.99"}
    values.update(overrides)
    return PresetIn(**values)


def test_preset_carries_the_bid_it_requires() -> None:
    """COST_CAP без `bid_amount` не собирается вовсе — заготовка без ставки
    неполна ровно в том поле, которое обязательно."""
    assert _preset(bid_strategy="COST_CAP", bid_amount="1.20").bid_amount == "1.20"


def test_preset_carries_strategy_and_display_link() -> None:
    preset = _preset(bid_strategy="LOWEST_COST_WITHOUT_CAP", display_link="play.ghana.com")

    assert preset.bid_strategy == "LOWEST_COST_WITHOUT_CAP"
    assert preset.display_link == "play.ghana.com"
```

- [ ] **Step 2: Убедиться, что падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaigns_create_schemas.py -q`
Expected: FAIL — `extra="forbid"` не пропускает `bid_amount`.

- [ ] **Step 3: Добавить поля в схему**

В `apps/api/routers/v1/schemas/campaigns_create.py`, в `PresetIn` после `daily_budget`:

```python
    bid_strategy: BidStrategy = "COST_CAP"
    # Ставка — часть заготовки, а не то, что вводят заново каждый раз:
    # COST_CAP без неё не собирается.
    bid_amount: str = Field(
        default="",
        strict=True,
        pattern=r"^(?:|(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)$",
        max_length=32,
    )
    display_link: str = Field(default="", max_length=255)
```

И импорт наверху файла:

```python
from core.campaign_drafts.contracts import BidStrategy
```

- [ ] **Step 4: Проверить хранилище пресетов**

Run: `grep -n "preset" core/campaign_drafts/repository.py`
Если пресет лежит одним JSONB-снимком — миграция не нужна. Если отдельными колонками — заведи
ревизию с `bid_strategy TEXT NOT NULL DEFAULT 'COST_CAP'`, `bid_amount TEXT NOT NULL DEFAULT ''`,
`display_link TEXT NOT NULL DEFAULT ''`.

- [ ] **Step 5: Гейты и коммит**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
git add -A && git commit -m "feat(campaigns): пресет хранит ставку, стратегию и отображаемую ссылку"
```

---

### Task A5: ОТМЕНЕНА — подстановка из оффера уже работает

Задача заводилась по ошибочному разбору: я посмотрел только бэкендовый SQL
(`_require_offer_scope` читает оффер лишь ради сверки CPA и валюты) и заключил,
что пиксель и гео оператор вбивает руками.

Это неверно. Подстановка живёт во фронте и работает:
`frontend/src/components/domain/campaigns/WizardStep2Identity.tsx:151` кладёт
`offer.pixel_id`, строка 155 — `offer.countries`, строка 163 — целевой CPA из
правил оффера после точной сверки валюты кабинета. Обработчик подключён:
`onGoalChange={store.setGoal}` в `frontend/src/routes/campaigns/create/index.tsx:312`.

Делать нечего. Задача снята, кода по ней не будет.

## Что требуется от тебя

### 1. Кабинет `1282495953856981` не читается

`Object with ID does not exist, cannot be loaded due to missing permissions`. Либо ID с
опечаткой, либо профилю стола не выдан доступ. Остальные три прочитались.

### 2. Task A1 меняет money-путь — знай об этом

Задача добавляет в каждую создаваемую группу `targeting_optimization: expansion_all`,
`brand_safety_content_filter_levels` и подфлаги Advantage+. Основание — 360 живых групп, но
это расширение аудитории за пределы заданной. Если для какого-то оффера нужен строгий таргет
без расширения — скажи до реализации, сделаю переключателем.

### 3. Ссылка на тот блог

Всё ещё не прислана. Если там что-то про актуальность настроек — часть выводов может
измениться.
