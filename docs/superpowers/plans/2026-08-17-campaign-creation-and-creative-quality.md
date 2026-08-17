# План доработки: создание кампаний, качество видео-креатива, перенос между кабинетами

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Снять с создания кампаний три самых дорогих замка (отображаемая ссылка, стратегия
ставок, неполный пресет), научить систему считать качество видео-креатива по hook/hold rate и
дать воспроизводимый перенос групп объявлений в другой кабинет.

**Architecture:** Три независимых трека. Трек A правит вертикальный slice создания —
контракт черновика, билдер тела Meta, пресет и оба фронта. Трек B добавляет видео-метрики в
уже существующий путь сбора (`am-fetch` → `ad_metrics`) и считает из них два коэффициента.
Трек C делает перенос через Marketing API вместо ручного Excel, потому что Excel не переносит
аудитории и пиксель между кабинетами.

**Tech Stack:** Python 3.11 + pydantic + SQLAlchemy Core + Alembic, FastAPI, Node.js
(browser-agent, session-tunneled Marketing API), React + Tailwind, pytest, vitest.

## Global Constraints

- Код, тесты и комментарии — по-русски; имена типов, API-полей и технических
  идентификаторов остаются английскими.
- Money-путь: сначала инвариант и regression test, потом правка.
- Один архитектурный слой или один вертикальный slice за PR.
- `null` — unknown, `0` — подтверждённый ноль. Пропуск метрики не превращается в ноль.
- Деньги и точные ratios передаются decimal strings.
- `pytest` только на изолированной БД. Прогон на боевой `:5433` сносит `offers`/`offer_rules`.
- Кампании со спендом > 0 не удалять. Ничего не создавать и не менять в боевом кабинете без
  явного «да» владельца.
- Сверять OpenAPI, generated clients и оба frontend, если менялся контракт.
- Прод выкатывается только `gh workflow run Release --ref main -f bootstrap=false`;
  push в main ничего не выкатывает.

## Что установлено 17.08 (факты, не предположения)

**Проверено на боевом кабинете A-8186 (скриншот стола, 14:07):** аккаунт ограничен —
«Для показа рекламы требуется подтверждение», все 8 кампаний в статусе «Аккаунт отключен»,
кнопка «Создать» погашена (при живых «Анализ» и «A/B-тестирование»). Мастер создания в
Ads Manager сейчас не открывается, сверка «экран против экрана» невозможна до подтверждения.

**Проверено по коду:**

- Опции создания зашиты как pydantic `Literal` с ЕДИНСТВЕННЫМ значением, то есть это не
  дефолты, а единственное допустимое: `objective=OUTCOME_SALES`,
  `optimization_goal=OFFSITE_CONVERSIONS`, `custom_event_type=PURCHASE`,
  `bid_strategy=COST_CAP`, `text_optimizations=OPT_OUT`, `currency=USD`.
  В `core/campaign_builder/builder.py` дополнительно намертво `billing_event=IMPRESSIONS`,
  `destination_type=WEBSITE`, `special_ad_categories=["NONE"]`.
- **Механизма актуализации нет вообще.** Ни одного места в коде, где мы читали бы у Meta
  справочник допустимых значений. Любая новая опция — правка контракта, а не настройка.
- **Отставание уже материализовалось:** в кабинете живут кампании со стратегиями
  «Максимальное количество» и «Используется стратегия ставок…», а наш создатель умеет
  выпускать только «Цель по цене за результат» (`COST_CAP`).
- `caption` (отображаемая ссылка) не используется нигде: единственные совпадения по слову —
  ffmpeg-оверлеи в `core/creatives/video_overlay.py` и Telegram, к креативам Meta отношения
  не имеют.
- Видео-метрик нет ни одной: `thruplay`, `video_p25…p100`, `video_play_curve`,
  `video_avg_time` не встречаются в коде ни разу.
- `ad_metrics` содержит: `spend, reach, impressions, clicks, cpc, ctr, cost_per_result, cpm,
  frequency, leads, cost_per_lead, registrations, cost_per_registration, deposits,
  outbound_clicks, outbound_ctr, landing_page_views, cost_per_landing_page_view`.
  Ни одного видео-поля.
- Пресет (`apps/api/routers/v1/schemas/campaigns_create.py:285`) хранит `name, countries,
  age_min, age_max, genders, placements, custom_event_type, budget_level, daily_budget,
  url_tags_template, naming_template` — и **не хранит `bid_amount`**, хотя единственная
  разрешённая стратегия `COST_CAP` без него падает валидацией
  (`core/campaign_builder/config.py:125`). Загрузка пресета всё равно требует ввести ставку.

**Проверено по документации Meta:** поле `caption` в `AdCreativeLinkData` — это и есть
отображаемая ссылка: «overwrites the caption under the title in the link», и Meta требует,
чтобы значение было настоящим URL и отражало домен назначения. Произвольный текст не подойдёт,
`play.ghana.com` — подойдёт.

**Проверено по обзору практики (не по доке Meta):** нативной кнопки «перенести в другой
кабинет» нет. Рабочий путь через Excel — экспорт из исходного кабинета, **удаление значений
ID**, импорт в целевой; оставленный ID превращает создание в перезапись оригинала. Настройки
групп (гео, возраст, площадки) в файле едут, а **пиксель, страница и кастомные аудитории —
account-scoped и не переносятся**: их колонки нужно переназначать на ID целевого кабинета.
Шаблон — 140+ колонок, `.xlsx`, лимит 2 МБ. Отсюда решение трека C: делать перенос через API,
а Excel оставить ручным запасным путём.

**Про эталоны 40% / 10% — обе метрики у Meta РОДНЫЕ.** Проверено на живом экране
(скриншот вкладки «Объявления → Статистика», 14:38): карточка «Результативность видео»
показывает четыре поля Meta — «Воспроизведения видео», «Среднее время воспроизведения видео»,
**«Коэффициент захвата внимания»** и **«Коэффициент удержания»**. Отраслевые блоги утверждают,
что таких метрик у Meta нет и их считают пользовательскими формулами; экран показывает
обратное, и верим экрану. Отсюда прямое следствие для трека B: **свои знаменатели мы не
выбираем**, а воспроизводим формулу Meta — иначе наши проценты не сойдутся с теми, на которые
владелец смотрит в кабинете.

Живой замер, который служит приёмочным тестом (объявление `52599529672016`, креатив
`CR2_CR003`, период 2026-08-10 … 2026-08-17):

| Поле | Значение |
|---|---|
| Воспроизведения видео | 315 |
| Среднее время воспроизведения | 00:05 |
| Коэффициент захвата внимания | 43 % |
| Коэффициент удержания | 1.48 % |
| Продолжительность видео | 00:39 |

Там же строится график **«Время просмотра»**: по оси X — секунды ролика от 0:00 до его
длительности, по оси Y — доля аудитории от 100% до 0. Для этого креатива кривая падает со 100%
до ~43% за первые четыре секунды, до ~20% к 0:12 и до ~2% к концу. Это ровно та кривая, ради
которой в B1 ищется `video_play_curve_actions`: раз Meta её рисует, данные для неё есть.

Сам креатив — наглядный случай: захват 43% выше твоего ориентира 40%, а удержание 1.48% в семь
раз ниже ориентира 10%. Первый кадр работает, дальше ролик зрителя не держит.

## Порядок и границы

Три трека независимы и выкатываются по отдельности. Рекомендуемый порядок — A → B → C:
A самый дешёвый и снимает названный тобой замок, B даёт данные для решений по креативам,
C самый крупный и требует второго кабинета.

### Насколько велик разрыв: счёт по SDK самой Meta

Доки Meta рендерятся JS и внешнему читателю не отдаются, поэтому допустимые значения сняты из
официального `facebook-python-business-sdk` (файлы `adobjects/campaign.py`, `adobjects/adset.py`
ветки main) — это тот же перечень, что принимает API.

| Поле | Значений у Meta | Поддерживаем | Доля |
|---|---|---|---|
| `objective` | 21 (из них 6 актуальных `OUTCOME_*`) | 1 — `OUTCOME_SALES` | 1 из 6 |
| `bid_strategy` | 4 | 1 — `COST_CAP` | 1 из 4 |
| `optimization_goal` | 33 | 1 — `OFFSITE_CONVERSIONS` | 1 из 33 |
| `billing_event` | 11 | 1 — `IMPRESSIONS` (зашит) | 1 из 11 |
| `special_ad_categories` | 7 | 1 — `NONE` (зашит) | 1 из 7 |

Полные списки для двух полей, которые трогает этот план:

- `bid_strategy`: `COST_CAP`, `LOWEST_COST_WITHOUT_CAP`, `LOWEST_COST_WITH_BID_CAP`,
  `LOWEST_COST_WITH_MIN_ROAS`.
- `special_ad_categories`: `NONE`, `CREDIT`, `EMPLOYMENT`, `HOUSING`,
  `ISSUES_ELECTIONS_POLITICS`, `FINANCIAL_PRODUCTS_SERVICES`,
  **`ONLINE_GAMBLING_AND_GAMING`**.

**Про `ONLINE_GAMBLING_AND_GAMING` — РЕШЕНИЕ ВЛАДЕЛЬЦА 17.08: оставляем `NONE`.**
У Meta есть спец-категория ровно под вертикаль проекта, и мы её никогда не объявляли.
Владелец решил не менять: объявление категории заметно урезает доступную аудиторию, а выгоды
для его задач не даёт. Это решение рекламодателя, а не разработчика, и оно не подлежит
пересмотру со стороны кода — задачу на смену категории не заводить и в аудитах не поднимать.

**Сознательно НЕ входит в этот план** (называю, чтобы не выглядело забытым): остальные цели
кампании и цели оптимизации, детальный таргетинг по интересам, кастомные аудитории и похожие,
языки, гео мельче страны, позиции плейсментов и устройства, расписание показа, дата окончания,
бюджет на весь срок, лимит расходов, ограничение частоты. Это отдельный крупный кусок; его
имеет смысл планировать, когда будет с чем сверять мастер создания.

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `core/campaign_drafts/contracts.py` | Поля черновика: отображаемая ссылка, стратегия | A1, A2 |
| `core/campaign_builder/config.py` | Конфиг сборки: `display_link`, набор стратегий | A1, A2 |
| `core/campaign_builder/builder.py` | Тело Meta: `caption` в link_data, `bid_strategy` | A1, A2 |
| `apps/api/routers/v1/schemas/campaigns_create.py` | Схема пресета | A1, A3 |
| `frontend/src/routes/campaigns/` | Поле ссылки и выбор стратегии в вебе | A1, A2 |
| `migrations/versions/` | Видео-колонки `ad_metrics` | B2 |
| `services/browser-agent/src/am/am-fetch.ts` | Запрос видео-полей у Meta | B2 |
| `core/creative_quality.py` | Расчёт hook/hold rate, профиль досмотра, подсказка обрезки | B3, B4 |
| `core/creative_report.py` | Сводка креатив×кабинет за период кампании | B5 |
| `core/ai_assistant/creative_advice.py` | Факты для модели и короткие выводы с фолбэком | B6 |
| `core/ai_assistant/prompts/skills/creative_report.md` | Скил-промт аналитика креативов | B6 |
| `scripts/adset_clone.py` | Перенос группы в другой кабинет | C1 |

---

## Трек A — создание догоняет Ads Manager

### Task A1: Отображаемая ссылка

Названа тобой прямо: `play.ghana.com` вместо сырого домена трекера.

**Files:**
- Modify: `core/campaign_drafts/contracts.py:57` (в `CampaignDraftGoal`)
- Modify: `core/campaign_builder/config.py:291` (в `CampaignConfig`)
- Modify: `core/campaign_builder/builder.py` (функция `_link_data`)
- Test: `tests/unit/test_campaign_builder.py`, `tests/unit/test_campaign_drafts.py`

**Interfaces:**
- Produces: `CampaignConfig.display_link: str` (пустая строка = поле не слать) и
  `CampaignDraftGoal.display_link: str`. Билдер кладёт значение в `link_data["caption"]`.

- [ ] **Step 1: Написать падающий тест билдера**

Добавь в `tests/unit/test_campaign_builder.py`:

```python
def test_display_link_reaches_meta_as_caption() -> None:
    """Отображаемая ссылка — это `caption` в link_data, а не отдельная сущность.

    Meta требует, чтобы значение было настоящим URL и отражало домен
    назначения; произвольный текст она отклоняет.
    """
    cfg = _config(destination_link="https://trk.example.com/click?c=1", display_link="play.ghana.com")

    link_data = _link_data(cfg, {"image_hash": "abc"})

    assert link_data["caption"] == "play.ghana.com"
    # Ссылка назначения остаётся настоящей: caption только подписывает её.
    assert link_data["link"] == "https://trk.example.com/click?c=1"


def test_display_link_is_omitted_when_not_set() -> None:
    """Пустой caption Meta трактует как ошибку, а не как «показать домен»."""
    cfg = _config(destination_link="https://trk.example.com/click?c=1")

    assert "caption" not in _link_data(cfg, {"image_hash": "abc"})
```

Если в файле нет хелпера `_config`, используй фактический конструктор `CampaignConfig` из
соседних тестов этого же файла — сигнатуру не выдумывай, скопируй у ближайшего теста.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q -k display_link`
Expected: FAIL — `CampaignConfig` не принимает `display_link`.

- [ ] **Step 3: Добавить поле в конфиг сборки**

В `core/campaign_builder/config.py`, в `CampaignConfig` рядом с `cta`:

```python
    # Отображаемая ссылка под заголовком (link_data.caption). Meta требует
    # настоящий URL, отражающий домен назначения: произвольный текст она
    # отклоняет. Пустая строка означает «поле не слать» — пустой caption
    # трактуется как ошибка, а не как «показать домен назначения».
    display_link: str = ""
```

- [ ] **Step 4: Класть caption в тело креатива**

В `core/campaign_builder/builder.py`, в `_link_data`, сразу после `ld.update(media)`:

```python
    if cfg.display_link:
        ld["caption"] = cfg.display_link
```

- [ ] **Step 5: Прогнать тест билдера**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_builder.py -q`
Expected: PASS.

- [ ] **Step 6: Написать падающий тест контракта черновика**

Добавь в `tests/unit/test_campaign_drafts.py`:

```python
import pytest

from core.campaign_drafts.contracts import CampaignDraftGoal


def test_draft_keeps_display_link() -> None:
    goal = CampaignDraftGoal(display_link="play.ghana.com")

    assert goal.display_link == "play.ghana.com"


@pytest.mark.parametrize("value", ["играй тут", "play ghana", "http://", "ghana"])
def test_draft_rejects_a_display_link_that_is_not_a_url(value: str) -> None:
    """Meta отклонит такой креатив уже на создании — ловим раньше неё.

    Иначе владелец узнаёт об ошибке из невнятного ответа Meta посреди залива.
    """
    with pytest.raises(ValueError):
        CampaignDraftGoal(display_link=value)
```

- [ ] **Step 7: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py -q -k display_link`
Expected: FAIL — `extra="forbid"` не пропускает неизвестное поле `display_link`.

- [ ] **Step 8: Добавить поле в контракт черновика**

В `core/campaign_drafts/contracts.py`, в `CampaignDraftGoal` после `cta`:

```python
    display_link: str = Field(default="", max_length=255)
```

И валидатор в том же классе, рядом с `validate_countries`:

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

`re` в этом файле уже импортирован (строка 9).

- [ ] **Step 9: Прогнать оба файла тестов**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py tests/unit/test_campaign_builder.py -q`
Expected: PASS.

- [ ] **Step 10: Пробросить поле в API-схему и оба фронта**

Найди, где `CampaignDraftGoal` превращается в запрос создания
(`grep -rn "destination_link" apps/api frontend/src frontend-mini/src`), и добавь
`display_link` тем же способом, каким там уже проброшен `destination_link` — отдельного
контракта для него заводить не нужно. В форме поле ставится под ссылкой назначения с
подписью «Отображаемая ссылка» и подсказкой «Например, play.ghana.com».

- [ ] **Step 11: Обновить контракт и прогнать гейты**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
```
Expected: PASS, дрейфа контракта нет.

- [ ] **Step 12: Коммит**

```bash
git add core/campaign_drafts/contracts.py core/campaign_builder/config.py core/campaign_builder/builder.py tests/unit/test_campaign_drafts.py tests/unit/test_campaign_builder.py apps/api frontend/src frontend-mini/src frontend/openapi.json packages/shared/src/api/generated.ts
git commit -m "feat(campaigns): отображаемая ссылка в креативе"
```

---

### Task A2: Стратегия ставок перестаёт быть одной

В кабинете уже крутятся кампании на «Максимальное количество», которые наш создатель
воспроизвести не может.

**Files:**
- Modify: `core/campaign_drafts/contracts.py:64`
- Modify: `core/campaign_builder/config.py:100,125`
- Test: `tests/unit/test_campaign_drafts.py`, `tests/unit/test_campaign_builder.py`

**Interfaces:**
- Consumes: `CampaignConfig.display_link` из A1 (файлы те же, конфликт правок исключён порядком).
- Produces: `BidStrategy = Literal["COST_CAP", "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP"]`
  в `core/campaign_drafts/contracts.py`, импортируемый схемой пресета в A3.

- [ ] **Step 1: Написать падающий тест**

Добавь в `tests/unit/test_campaign_drafts.py`:

```python
def test_draft_allows_the_strategies_the_cabinet_actually_runs() -> None:
    """В кабинете живут кампании на «Максимальное количество».

    Замок на COST_CAP означал, что наш создатель не может воспроизвести
    половину того, что в кабинете уже работает.
    """
    assert CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP").bid_strategy == (
        "LOWEST_COST_WITHOUT_CAP"
    )
    assert CampaignDraftGoal(bid_strategy="LOWEST_COST_WITH_BID_CAP").bid_strategy == (
        "LOWEST_COST_WITH_BID_CAP"
    )


def test_uncapped_strategy_does_not_demand_a_bid() -> None:
    """`bid_amount` — поле кэпа. Требовать его у стратегии без кэпа значит
    закрыть её на замок, который сам же и придумал."""
    goal = CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP", bid_amount="")

    assert goal.bid_amount == ""
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py -q -k strategies`
Expected: FAIL — `Literal["COST_CAP"]` не принимает другое значение.

- [ ] **Step 3: Расширить контракт**

В `core/campaign_drafts/contracts.py` перед `class CampaignDraftGoal`:

```python
# Стратегии, которые кабинет уже использует. Список расширяется правкой кода
# осознанно: справочник у Meta мы не читаем, и молча появиться тут ничего не должно.
BidStrategy = Literal["COST_CAP", "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP"]
```

И в `CampaignDraftGoal` замени строку `bid_strategy: Literal["COST_CAP"] = "COST_CAP"` на:

```python
    bid_strategy: BidStrategy = "COST_CAP"
```

- [ ] **Step 4: Прогнать тесты контракта**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaign_drafts.py -q`
Expected: PASS.

- [ ] **Step 5: Проверить, что валидация ставки уже развязана**

В `core/campaign_builder/config.py:125` условие требует `bid_amount` только для
`_CAPPED_BID_STRATEGIES`. Убедись, что `LOWEST_COST_WITHOUT_CAP` в этот набор не входит:

Run: `grep -n "_CAPPED_BID_STRATEGIES" core/campaign_builder/config.py`
Expected: набор содержит только `COST_CAP` и `LOWEST_COST_WITH_BID_CAP`. Если
`LOWEST_COST_WITHOUT_CAP` там есть — убрать, иначе стратегия без кэпа потребует ставку.

- [ ] **Step 6: Дать выбор в обоих фронтах**

Найди место, где стратегия сейчас зашита:
`grep -rn "COST_CAP" frontend/src frontend-mini/src packages/shared/src`

Замени зашитое значение стратегии на селект из трёх вариантов с подписями, как в Ads Manager:
`COST_CAP` → «Цель по цене за результат», `LOWEST_COST_WITHOUT_CAP` → «Максимальное
количество», `LOWEST_COST_WITH_BID_CAP` → «Предельная ставка». Поле ставки показывается
только для двух последних из списка `_CAPPED_BID_STRATEGIES`.

- [ ] **Step 7: Гейты и коммит**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
git add -A && git commit -m "feat(campaigns): выбор стратегии ставок вместо единственной COST_CAP"
```

---

### Task A3: Пресет перестаёт быть неполным

Найдено при разборе: пресет не хранит `bid_amount`, хотя `COST_CAP` без него не собирается.

**Files:**
- Modify: `apps/api/routers/v1/schemas/campaigns_create.py:285-306`
- Modify: `migrations/versions/` (новая ревизия)
- Test: `tests/unit/test_campaigns_create_schemas.py`

**Interfaces:**
- Consumes: `BidStrategy` из A2.
- Produces: `PresetIn.bid_strategy: BidStrategy`, `PresetIn.bid_amount: str`,
  `PresetIn.display_link: str`.

- [ ] **Step 1: Написать падающий тест**

Создай `tests/unit/test_campaigns_create_schemas.py` (или допиши, если существует):

```python
import pytest

from apps.api.routers.v1.schemas.campaigns_create import PresetIn


def _preset(**overrides):
    values = {
        "name": "GH Aviator",
        "countries": ["GH"],
        "daily_budget": "8.99",
    }
    values.update(overrides)
    return PresetIn(**values)


def test_preset_carries_the_bid_it_requires() -> None:
    """COST_CAP без `bid_amount` не собирается (config.py:125).

    Пресет без ставки означал: загрузил заготовку — всё равно вводи ставку
    руками, то есть заготовка неполная ровно в том поле, которое обязательно.
    """
    preset = _preset(bid_strategy="COST_CAP", bid_amount="1.20")

    assert preset.bid_amount == "1.20"


def test_preset_carries_the_display_link() -> None:
    preset = _preset(display_link="play.ghana.com")

    assert preset.display_link == "play.ghana.com"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaigns_create_schemas.py -q`
Expected: FAIL — `extra="forbid"` не пропускает `bid_amount`.

- [ ] **Step 3: Добавить поля в схему пресета**

В `apps/api/routers/v1/schemas/campaigns_create.py`, в `PresetIn` после `daily_budget`:

```python
    bid_strategy: BidStrategy = "COST_CAP"
    # Ставка — часть заготовки, а не то, что вводят заново каждый раз:
    # COST_CAP без неё не собирается вовсе.
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

- [ ] **Step 4: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_campaigns_create_schemas.py -q`
Expected: PASS.

- [ ] **Step 5: Миграция хранилища пресетов**

Посмотри, как пресет хранится (`grep -n "preset" core/campaign_drafts/repository.py`).
Если поля лежат отдельными колонками — заведи ревизию, добавляющую `bid_strategy TEXT NOT NULL
DEFAULT 'COST_CAP'`, `bid_amount TEXT NOT NULL DEFAULT ''`, `display_link TEXT NOT NULL
DEFAULT ''`. Если пресет хранится одним JSONB-снимком — миграция не нужна, и этот шаг
закрывается проверкой, что существующие пресеты читаются без ошибки:

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q -k preset`
Expected: PASS.

- [ ] **Step 6: Гейты и коммит**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi
pnpm gen:api && pnpm -r typecheck && pnpm -r test
git add -A && git commit -m "feat(campaigns): пресет хранит ставку, стратегию и отображаемую ссылку"
```

---

## Трек B — качество видео-креатива

### Task B1: Снять имена колонок с самой страницы, а не подбирать их

Считать коэффициенты самим не нужно и не следует: Meta их уже посчитала и показывает в
карточке. Мы и так не изобретаем запросы к Ads Manager, а переиспользуем его собственные —
`services/browser-agent/src/am/am-fetch.ts` снифает `access_token` из исходящих запросов
страницы к `adsmanager-graph.facebook.com` (строка 52) и запрашивает метрики списком
`column_fields` (`AM_COLUMN_FIELDS` в `am-config.ts`, сейчас 18 колонок). Задача — узнать, как
называются колонки захвата и удержания, и добавить их в тот же список.

Своя формула нужна только как сверка. Из подсказок ⓘ (прислал владелец):
захват — «процент, сколько раз ваше видео было воспроизведено в течение как минимум 3 секунд»;
удержание — «процент, сколько раз ваше видео было воспроизведено до конца после воспроизведения
в течение как минимум 3 секунд», то есть досмотры ÷ трёхсекундные воспроизведения.
**Обе метрики Meta помечает «in development»** — имя колонки и само её существование могут
измениться, поэтому отсутствие колонки в ответе должно давать `unknown`, а не падение скана.

**Files:**
- Modify: `services/browser-agent/src/am/am-fetch.ts` (временный дамп в снифере)
- Create: `docs/meta-video-fields-2026-08.md`

- [ ] **Step 1: Временно расширить снифер до полного дампа запроса**

В `am-fetch.ts`, в обработчике `onReq` (строка ~62), рядом с существующей проверкой добавь
временный дамп — он нужен на один прогон и снимается в Step 4:

```ts
        // ВРЕМЕННО (снять в том же PR после снятия имён колонок): полный дамп
        // запросов страницы, чтобы увидеть, какими колонками карточка
        // «Результативность видео» просит захват и удержание.
        if (u.includes('am_tabular') || u.includes('insights')) {
          console.log('[am-sniff]', decodeURIComponent(u).slice(0, 4000));
        }
```

- [ ] **Step 2: Открыть карточку и снять лог**

Собери и перезапусти browser-agent, затем на столе открой вкладку «Объявления → Статистика» для
объявления `52599529672016` за период 2026-08-10 … 2026-08-17 и сними лог:

```bash
ssh root@62.60.150.133 'docker logs --since 5m fb_agent_desktop-browser-agent-1 2>&1 | grep "\[am-sniff\]"'
```
Expected: строки с `column_fields=[...]`, среди которых — колонки видео.

- [ ] **Step 3: Записать результат документом**

Создай `docs/meta-video-fields-2026-08.md` и зафиксируй: точные имена колонок захвата и
удержания, имя колонки кривой досмотра (если карточка просит её тем же запросом), форму
значения каждой и то, что обе метрики помечены «in development». Плюс контрольный замер для
приёмки: объявление `52599529672016`, период 2026-08-10 … 2026-08-17, ожидаемые 43% и 1.48%.

- [ ] **Step 4: Снять временный дамп и закоммитить**

Убери добавленный в Step 1 `console.log` — сырой URL несёт `access_token`, и в логе ему не
место.

```bash
cd services/browser-agent && npm run lint && npm test
git add docs/meta-video-fields-2026-08.md services/browser-agent/src/am/am-fetch.ts
git commit -m "docs(meta): имена колонок захвата и удержания сняты с самой страницы"
```

---

### Task B2: Видео-метрики попадают в хранилище

**Files:**
- Create: `migrations/versions/<rev>_ad_metrics_video.py`
- Modify: `services/browser-agent/src/am/am-fetch.ts`
- Modify: `core/observer/writers.py`
- Test: `tests/unit/test_ad_metrics_video.py`, `services/browser-agent/src/am/am-fetch.test.ts`

**Interfaces:**
- Consumes: список подтверждённых полей из `docs/meta-video-fields-2026-08.md` (B1).
- Produces: колонки `ad_metrics.video_plays_3s`, `.video_completions`,
  `.video_avg_time_watched_sec`, `.video_p25 … .video_p100` — все `BIGINT NULL` кроме
  `video_avg_time_watched_sec NUMERIC(10,2) NULL`.

- [ ] **Step 1: Написать падающий тест инварианта**

Создай `tests/unit/test_ad_metrics_video.py`:

```python
from core.observer.writers import build_ad_metrics_row


def test_missing_video_metric_stays_unknown_not_zero() -> None:
    """Пропуск метрики не превращается в ноль — инвариант проекта.

    Ноль просмотров и «Meta не отдала поле» ведут к противоположным решениям
    по креативу: первый значит «плохой», второй — «мы не знаем».
    """
    row = build_ad_metrics_row({"impressions": "100"})

    assert row["video_plays_3s"] is None
    assert row["video_completions"] is None


def test_zero_video_plays_is_a_confirmed_zero() -> None:
    row = build_ad_metrics_row({"impressions": "100", "video_plays_3s": "0"})

    assert row["video_plays_3s"] == 0
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_ad_metrics_video.py -q`
Expected: FAIL — ключей `video_plays_3s`/`video_completions` в строке нет.

Если функция называется иначе — найди фактическое имя
(`grep -n "def .*ad_metrics\|INSERT INTO ad_metrics" core/observer/writers.py`) и поправь
импорт в тесте, не выдумывая новое имя.

- [ ] **Step 3: Миграция**

Ревизия добавляет колонки, все `NULL`-евые — существующие строки не получают выдуманных нулей:

```python
def upgrade() -> None:
    op.add_column("ad_metrics", sa.Column("video_plays_3s", sa.BigInteger(), nullable=True))
    op.add_column("ad_metrics", sa.Column("video_completions", sa.BigInteger(), nullable=True))
    op.add_column(
        "ad_metrics",
        sa.Column("video_avg_time_watched_sec", sa.Numeric(10, 2), nullable=True),
    )
    for quartile in ("p25", "p50", "p75", "p100"):
        op.add_column(
            "ad_metrics", sa.Column(f"video_{quartile}", sa.BigInteger(), nullable=True)
        )
```

- [ ] **Step 4: Запрашивать поля у Meta и писать их**

В `am-fetch.ts` добавь подтверждённые в B1 поля к списку insights, в `writers.py` — перенос
значений в строку. Отсутствующее поле остаётся `None`, а не `0`.

- [ ] **Step 5: Прогнать тесты**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_ad_metrics_video.py -q
cd services/browser-agent && npm test
```
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add -A && git commit -m "feat(metrics): видео-метрики объявления доезжают до хранилища"
```

---

### Task B3: Брать коэффициенты у Meta, свой расчёт — только запасной

Значения считает Meta и отдаёт колонками (B1). Наша формула нужна ровно на два случая: колонка
не пришла (метрика «in development», её могут убрать) и сверка, что мы читаем то самое поле.

**Условие приёмки:** и колонка Meta, и запасной расчёт дают 43% и 1.48% для объявления
`52599529672016` за 2026-08-10 … 2026-08-17. Расхождение запасного расчёта с колонкой больше
чем на 0.1 п.п. означает, что формула понята неверно.

Формула запасного расчёта — прямо из подсказок Meta:
захват = трёхсекундные воспроизведения ÷ показы; удержание = досмотры до конца ÷
трёхсекундные воспроизведения. Ниже в коде именно она; имена полей возьми из
`docs/meta-video-fields-2026-08.md`.

**Files:**
- Create: `core/creative_quality.py`
- Test: `tests/unit/test_creative_quality.py`

**Interfaces:**
- Consumes: колонки из B2.
- Produces: `creative_quality(plays_3s, completions, impressions) -> CreativeQuality` с полями
  `hook_rate: Decimal | None`, `hold_rate: Decimal | None`, `verdict: Literal["ok","warning","unknown"]`.

- [ ] **Step 1: Написать падающий тест**

Создай `tests/unit/test_creative_quality.py`:

```python
from decimal import Decimal

from core.creative_quality import creative_quality


def test_hook_and_hold_use_the_denominators_we_declared() -> None:
    """Знаменатель фиксируем в коде: отрасль считает hold rate тремя разными
    Знаменатели взяты из подсказок Meta, а не выбраны нами: захват — от
    показов, удержание — от трёхсекундных воспроизведений. Это запасной путь
    на случай, когда колонка Meta не пришла.
    """
    quality = creative_quality(plays_3s=400, completions=60, impressions=1000)

    assert quality.hook_rate == Decimal("40.00")  # 400 / 1000
    assert quality.hold_rate == Decimal("15.00")  # 60 / 400
    assert quality.verdict == "ok"


def test_below_reference_is_a_warning_not_a_failure() -> None:
    """Эталоны 40% и 10% — ориентир владельца, а не отказ системы."""
    quality = creative_quality(plays_3s=150, completions=10, impressions=1000)

    assert quality.verdict == "warning"


def test_unknown_stays_unknown() -> None:
    """Нет данных — нет вердикта. Ноль тут соврал бы «плохой креатив»."""
    assert creative_quality(plays_3s=None, completions=None, impressions=1000).verdict == "unknown"


def test_no_impressions_is_not_a_division_by_zero() -> None:
    assert creative_quality(plays_3s=0, completions=0, impressions=0).hook_rate is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_quality.py -q`
Expected: FAIL — модуля `core/creative_quality.py` нет.

- [ ] **Step 3: Реализовать**

Создай `core/creative_quality.py`:

```python
"""Качество видео-креатива двумя коэффициентами с явным знаменателем.

Официального эталона у Meta нет: hook rate там вообще не метрика, а
пользовательская формула, и отрасль считает hold rate тремя разными способами.
Поэтому знаменатели зафиксированы здесь и подписаны в UI — иначе «10%» будет
значить разное в разные дни.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

# Ориентиры владельца, а не отказ системы: ниже них креатив помечается
# предупреждением, но ничего не останавливается.
HOOK_RATE_REFERENCE = Decimal("40")
HOLD_RATE_REFERENCE = Decimal("10")


@dataclass(frozen=True)
class CreativeQuality:
    hook_rate: Decimal | None
    hold_rate: Decimal | None
    verdict: Literal["ok", "warning", "unknown"]


def _ratio(numerator: int | None, denominator: int | None) -> Decimal | None:
    if numerator is None or not denominator:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def creative_quality(
    *, plays_3s: int | None, completions: int | None, impressions: int | None
) -> CreativeQuality:
    hook_rate = _ratio(plays_3s, impressions)
    hold_rate = _ratio(completions, plays_3s)
    if hook_rate is None or hold_rate is None:
        return CreativeQuality(hook_rate, hold_rate, "unknown")
    verdict = (
        "ok"
        if hook_rate >= HOOK_RATE_REFERENCE and hold_rate >= HOLD_RATE_REFERENCE
        else "warning"
    )
    return CreativeQuality(hook_rate, hold_rate, verdict)
```

- [ ] **Step 4: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_quality.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/creative_quality.py tests/unit/test_creative_quality.py
git commit -m "feat(creatives): hook rate и hold rate с зафиксированным знаменателем"
```

---

### Task B4: Где ролик теряет зрителя и стоит ли его резать

Названо тобой: по графику «время просмотра / процент аудитории» решать, не обрезать ли длинное
видео. Считаем по квартилям, а не по посекундной кривой: квартили — обычные поля insights,
существование которых не под вопросом, а `video_play_curve_actions` подтверждается только в B1.

**Files:**
- Modify: `core/creative_quality.py`
- Test: `tests/unit/test_creative_quality.py`

**Interfaces:**
- Consumes: колонки `video_p25 … video_p100` из B2.
- Produces: `retention_profile(p25, p50, p75, p100, *, plays_3s) -> RetentionProfile` с полями
  `kept: dict[str, Decimal | None]` (доля от 3-секундных просмотров на каждой четверти),
  `steepest_drop: str | None` (`"p25→p50"` и т.п.), `advice: Literal["trim","keep","unknown"]`.

- [ ] **Step 1: Написать падающий тест**

Допиши в `tests/unit/test_creative_quality.py`:

```python
from core.creative_quality import retention_profile


def test_profile_shows_where_the_viewer_leaves() -> None:
    """Обрезать имеет смысл там, где обрыв самый резкий, а не «в середине».

    Здесь зритель уходит между первой и второй четвертью: 300 → 90.
    """
    profile = retention_profile(p25=300, p50=90, p75=80, p100=75, plays_3s=400)

    assert profile.kept["p25"] == Decimal("75.00")  # 300 / 400
    assert profile.steepest_drop == "p25→p50"
    assert profile.advice == "trim"


def test_evenly_watched_video_is_not_worth_cutting() -> None:
    """Обрыв есть всегда — важно, насколько он выделяется.

    Здесь потери 5 / 10.5 / 11.8 / 6.7 процента: самый крупный шаг всё равно
    втрое ниже порога, и резать нечего.
    """
    profile = retention_profile(p25=380, p50=340, p75=300, p100=280, plays_3s=400)

    assert profile.steepest_drop == "p50→p75"
    assert profile.advice == "keep"


def test_profile_without_data_gives_no_advice() -> None:
    """Совет резать ролик по пустым данным — худший вид уверенности."""
    profile = retention_profile(p25=None, p50=None, p75=None, p100=None, plays_3s=400)

    assert profile.advice == "unknown"
    assert profile.steepest_drop is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_quality.py -q -k retention`
Expected: FAIL — `cannot import name 'retention_profile'`.

- [ ] **Step 3: Реализовать**

Допиши в `core/creative_quality.py`:

```python
# Обрыв круче этой доли между соседними четвертями означает, что зритель
# уходит в конкретном месте, а не растекается равномерно — ролик стоит резать
# до этой точки. Порог наш, не отраслевой: он лишь делит «ровный досмотр» и
# «обвал», и подписан в UI как ориентир.
STEEP_DROP_SHARE = Decimal("50")


@dataclass(frozen=True)
class RetentionProfile:
    kept: dict[str, Decimal | None]
    steepest_drop: str | None
    advice: Literal["trim", "keep", "unknown"]


def retention_profile(
    *,
    p25: int | None,
    p50: int | None,
    p75: int | None,
    p100: int | None,
    plays_3s: int | None,
) -> RetentionProfile:
    quartiles = {"p25": p25, "p50": p50, "p75": p75, "p100": p100}
    kept = {name: _ratio(value, plays_3s) for name, value in quartiles.items()}
    steps = [("p3s→p25", plays_3s, p25), ("p25→p50", p25, p50), ("p50→p75", p50, p75),
             ("p75→p100", p75, p100)]
    losses: list[tuple[str, Decimal]] = []
    for label, before, after in steps:
        if before is None or after is None or not before:
            continue
        losses.append((label, Decimal(100) - (Decimal(after) * 100 / Decimal(before))))
    if not losses:
        return RetentionProfile(kept, None, "unknown")
    label, loss = max(losses, key=lambda item: item[1])
    return RetentionProfile(kept, label, "trim" if loss >= STEEP_DROP_SHARE else "keep")
```

- [ ] **Step 4: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_quality.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/creative_quality.py tests/unit/test_creative_quality.py
git commit -m "feat(creatives): профиль досмотра и подсказка про обрезку ролика"
```

---

### Task B5: Сводка по креативам за период кампании

Ты дал ссылку на вкладку Insights с готовым набором: кабинет, кампания, конкретные объявления,
период `2026-08-14 … today`. **Скрейпить эту ссылку не нужно и не надо** — за ней стоят те же
insights, что отдаёт API, только API берёт произвольный `time_range` и произвольный список
объявлений сразу, без браузера и без сессии в UI. Ссылка ценна как спецификация: она называет,
какие именно числа и за какой период ты хочешь видеть.

Ключ группировки — **креатив × кабинет**, а не просто креатив. Ты сам это назвал: один и тот же
ролик в разных кабинетах на разных аудиториях идёт по-разному, и усреднение по креативу спрятало
бы ровно тот сигнал, ради которого сводка и делается.

**Files:**
- Create: `core/creative_report.py`
- Test: `tests/unit/test_creative_report.py`

**Interfaces:**
- Consumes: `creative_quality` и `retention_profile` из B3/B4, колонки из B2.
- Produces: `campaign_period(campaign_start: date, today: date) -> tuple[str, str]` и
  `creative_rollup(rows: list[dict]) -> list[CreativeRollup]`, где `CreativeRollup` несёт
  `creative_key: str`, `ad_account_id: str`, `impressions: int`, `spend: Decimal`,
  `quality: CreativeQuality`, `retention: RetentionProfile`.

- [ ] **Step 1: Написать падающий тест**

Создай `tests/unit/test_creative_report.py`:

```python
from datetime import date
from decimal import Decimal

from core.creative_report import campaign_period, creative_rollup


def test_period_runs_from_campaign_start_to_today() -> None:
    """Период считается от старта кампании, а не «последние 7 дней».

    Кампания от 14-го и смотрится с 14-го: фиксированное окно обрезало бы
    ранние дни, на которых креатив как раз и разгонялся.
    """
    assert campaign_period(date(2026, 8, 14), date(2026, 8, 17)) == ("2026-08-14", "2026-08-17")


def _row(**overrides):
    values = {
        "creative_key": "video:777",
        "ad_account_id": "996458953428822",
        "impressions": 1000,
        "spend": Decimal("10.00"),
        "video_plays_3s": 400,
        "video_completions": 60,
        "video_p25": 300,
        "video_p50": 90,
        "video_p75": 80,
        "video_p100": 75,
    }
    values.update(overrides)
    return values


def test_same_creative_in_two_cabinets_stays_two_rows() -> None:
    """Один ролик в разных кабинетах идёт по-разному — усреднение спрятало бы
    ровно тот сигнал, ради которого сводка и собирается."""
    rollup = creative_rollup([_row(), _row(ad_account_id="1386439193678186", video_plays_3s=150)])

    assert len(rollup) == 2
    assert {item.ad_account_id for item in rollup} == {"996458953428822", "1386439193678186"}


def test_rollup_carries_quality_and_retention() -> None:
    rollup = creative_rollup([_row()])

    assert rollup[0].quality.hook_rate == Decimal("40.00")
    assert rollup[0].retention.steepest_drop == "p25→p50"


def test_rows_of_one_creative_in_one_cabinet_are_summed() -> None:
    """Несколько объявлений с одним роликом в одном кабинете — это один
    креатив: складываем, а не показываем четырьмя строками."""
    rollup = creative_rollup([_row(), _row(impressions=1000, video_plays_3s=400)])

    assert len(rollup) == 1
    assert rollup[0].impressions == 2000
    assert rollup[0].spend == Decimal("20.00")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_report.py -q`
Expected: FAIL — модуля `core/creative_report.py` нет.

- [ ] **Step 3: Реализовать**

Создай `core/creative_report.py`:

```python
"""Сводка по видео-креативам за период жизни кампании.

Период считается от старта кампании до сегодня: фиксированное окно
«последние N дней» обрезало бы дни разгона. Ключ группировки — креатив И
кабинет: один ролик в разных кабинетах на разных аудиториях идёт по-разному,
и усреднение по креативу спрятало бы этот сигнал.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from core.creative_quality import CreativeQuality, RetentionProfile, creative_quality, retention_profile


@dataclass(frozen=True)
class CreativeRollup:
    creative_key: str
    ad_account_id: str
    impressions: int
    spend: Decimal
    quality: CreativeQuality
    retention: RetentionProfile


def campaign_period(campaign_start: date, today: date) -> tuple[str, str]:
    return campaign_start.isoformat(), today.isoformat()


def _add(left: int | None, right: int | None) -> int | None:
    """Сумма, в которой unknown остаётся unknown, а не превращается в ноль."""
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def creative_rollup(rows: list[dict]) -> list[CreativeRollup]:
    buckets: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["creative_key"], row["ad_account_id"])
        bucket = buckets.setdefault(
            key,
            {"impressions": 0, "spend": Decimal("0"), "video_plays_3s": None,
             "video_completions": None, "video_p25": None, "video_p50": None,
             "video_p75": None, "video_p100": None},
        )
        bucket["impressions"] += int(row["impressions"] or 0)
        bucket["spend"] += Decimal(str(row["spend"] or "0"))
        for field in ("video_plays_3s", "video_completions", "video_p25", "video_p50",
                      "video_p75", "video_p100"):
            bucket[field] = _add(bucket[field], row.get(field))

    result: list[CreativeRollup] = []
    for (creative_key, ad_account_id), bucket in buckets.items():
        result.append(
            CreativeRollup(
                creative_key=creative_key,
                ad_account_id=ad_account_id,
                impressions=bucket["impressions"],
                spend=bucket["spend"],
                quality=creative_quality(
                    plays_3s=bucket["video_plays_3s"],
                    completions=bucket["video_completions"],
                    impressions=bucket["impressions"],
                ),
                retention=retention_profile(
                    p25=bucket["video_p25"],
                    p50=bucket["video_p50"],
                    p75=bucket["video_p75"],
                    p100=bucket["video_p100"],
                    plays_3s=bucket["video_plays_3s"],
                ),
            )
        )
    return result
```

- [ ] **Step 4: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_report.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/creative_report.py tests/unit/test_creative_report.py
git commit -m "feat(creatives): сводка по креативам за период кампании, ключ креатив×кабинет"
```

---

### Task B6: Выводы по креативам от ассистента

Ассистент в проекте уже есть и работает ровно нужным образом: `core/ai_assistant/pulse.py`
собирает факты, грузит скил-промт через `load_skill`, зовёт `AIClient.chat` и **при
недоступности модели отдаёт детерминированный фолбэк вместо молчания**. Повторяем этот
контракт, а не изобретаем новый.

**Files:**
- Create: `core/ai_assistant/prompts/skills/creative_report.md`
- Create: `core/ai_assistant/creative_advice.py`
- Test: `tests/unit/test_creative_advice.py`

**Interfaces:**
- Consumes: `CreativeRollup` из B5, `AIClient` из `core/ai_assistant/client.py`,
  `load_skill` из `core/ai_assistant/prompts.py`.
- Produces: `build_creative_advice(rollups: list[CreativeRollup], *, period: tuple[str, str]) -> str`.

- [ ] **Step 1: Написать промт-скил**

Создай `core/ai_assistant/prompts/skills/creative_report.md`:

```markdown
Ты байер-аналитик. На входе — таблица фактов по видео-креативам за период.

Правила ответа:
- Не более шести строк. Каждая строка — вывод и что с ним делать.
- Начинай с худшего креатива: с него теряются деньги.
- hook rate — доля показов, досмотревших до 3 секунд; ориентир 40%.
  Коэффициент удержания — доля 3-секундных воспроизведений, досмотренных до
  конца; ориентир 10%.
- Низкий hook — проблема первого кадра. Низкий hold при высоком hook — обещание
  в начале не выполнено дальше. Резкий обрыв на конкретной четверти — ролик
  стоит обрезать до этой точки.
- Один и тот же креатив в разных кабинетах сравнивай между собой: расхождение
  означает аудиторию, а не сам ролик.
- Не выдумывай чисел, которых нет во входных фактах. Если данных по креативу
  нет, так и скажи одной строкой.
- Без вступлений, без пересказа таблицы, без Markdown-разметки.
```

- [ ] **Step 2: Написать падающий тест**

Создай `tests/unit/test_creative_advice.py`:

```python
import pytest

from core.ai_assistant.creative_advice import build_creative_advice, facts_table
from core.creative_report import creative_rollup


def _rollups():
    return creative_rollup(
        [
            {
                "creative_key": "video:777",
                "ad_account_id": "996458953428822",
                "impressions": 1000,
                "spend": "10.00",
                "video_plays_3s": 400,
                "video_completions": 60,
                "video_p25": 300,
                "video_p50": 90,
                "video_p75": 80,
                "video_p100": 75,
            }
        ]
    )


def test_facts_name_the_cabinet_and_both_rates() -> None:
    """Модель не должна догадываться, из какого кабинета строка."""
    table = facts_table(_rollups(), period=("2026-08-14", "2026-08-17"))

    assert "996458953428822" in table
    assert "video:777" in table
    assert "40.00" in table  # hook rate
    assert "15.00" in table  # hold rate
    assert "2026-08-14" in table


@pytest.mark.asyncio
async def test_advice_falls_back_instead_of_going_silent(monkeypatch) -> None:
    """Пульс при недоступной модели отдаёт детерминированный текст — здесь тот
    же контракт: молчание выглядело бы как «всё в порядке»."""
    from core.ai_assistant import creative_advice as module

    class _Down:
        is_available = False

    monkeypatch.setattr(module, "get_ai_client", lambda *_a, **_k: _Down())

    text = await build_creative_advice(_rollups(), period=("2026-08-14", "2026-08-17"))

    assert "video:777" in text
    assert text.strip() != ""
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_advice.py -q`
Expected: FAIL — модуля `core/ai_assistant/creative_advice.py` нет.

- [ ] **Step 4: Реализовать**

Создай `core/ai_assistant/creative_advice.py`:

```python
"""Короткие выводы по видео-креативам от ассистента.

Тот же контракт, что у пульса (`core/ai_assistant/pulse.py`): факты собираются
детерминированно, модель только формулирует. При недоступной модели отдаём
таблицу фактов, а не молчание — молчание выглядело бы как «всё в порядке».
"""

from __future__ import annotations

import asyncio

from core.ai_assistant.client import get_ai_client
from core.ai_assistant.prompts import PromptNotFoundError, load_skill
from core.creative_report import CreativeRollup

_AI_TIMEOUT_SECONDS = 60.0


def facts_table(rollups: list[CreativeRollup], *, period: tuple[str, str]) -> str:
    """Факты для модели. Пустое значение — честный прочерк, а не ноль."""
    lines = [f"Период: {period[0]} — {period[1]}", "креатив | кабинет | показы | расход | hook % | hold % | обрыв | совет"]
    for item in rollups:
        hook = "—" if item.quality.hook_rate is None else f"{item.quality.hook_rate}"
        hold = "—" if item.quality.hold_rate is None else f"{item.quality.hold_rate}"
        drop = item.retention.steepest_drop or "—"
        lines.append(
            f"{item.creative_key} | {item.ad_account_id} | {item.impressions} | "
            f"{item.spend} | {hook} | {hold} | {drop} | {item.retention.advice}"
        )
    return "\n".join(lines)


async def build_creative_advice(
    rollups: list[CreativeRollup], *, period: tuple[str, str]
) -> str:
    facts = facts_table(rollups, period=period)
    client = get_ai_client()
    if not client.is_available:
        return facts

    try:
        system = load_skill("creative_report")
    except PromptNotFoundError:
        system = "Ты байер-аналитик. Дай короткие выводы по фактам о видео-креативах."

    try:
        response = await asyncio.wait_for(
            client.chat(messages=[{"role": "user", "content": facts}], system=system),
            timeout=_AI_TIMEOUT_SECONDS,
        )
    except Exception:
        return facts
    return response.strip() or facts
```

Сверь сигнатуру `client.chat` и тип его ответа с фактическим кодом
(`core/ai_assistant/client.py:40`) и с тем, как её зовёт `pulse.py:220` — если там
возвращается не строка, приведи к строке тем же способом, что и пульс.

- [ ] **Step 5: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_creative_advice.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add core/ai_assistant/creative_advice.py core/ai_assistant/prompts/skills/creative_report.md tests/unit/test_creative_advice.py
git commit -m "feat(creatives): короткие выводы по креативам от ассистента с фолбэком"
```

---

## Трек C — перенос группы объявлений в другой кабинет

### Task C1: Клон группы через API вместо Excel

Excel-путь работает, но теряет ровно то, ради чего его затевают: пиксель, страница и кастомные
аудитории — account-scoped, их колонки всё равно переназначают руками. API-клон делает то же
самое явно и повторяемо.

**Files:**
- Create: `scripts/adset_clone.py`
- Test: `tests/unit/test_adset_clone.py`

**Interfaces:**
- Produces: `plan_adset_clone(source: dict, *, target_pixel_id: str, target_page_id: str) -> dict`
  — чистая функция, отдающая тело для создания группы в целевом кабинете.

- [ ] **Step 1: Написать падающий тест**

Создай `tests/unit/test_adset_clone.py`:

```python
import pytest

from scripts.adset_clone import plan_adset_clone


def _source():
    return {
        "id": "1234",
        "name": "GH | CR2 | 14.08",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
        "daily_budget": "899",
        "bid_strategy": "COST_CAP",
        "bid_amount": "120",
        "targeting": {
            "geo_locations": {"countries": ["GH"]},
            "age_min": 21,
            "age_max": 65,
            "publisher_platforms": ["facebook", "instagram"],
        },
        "promoted_object": {"pixel_id": "SOURCE_PIXEL", "custom_event_type": "PURCHASE"},
    }


def test_clone_carries_targeting_and_budget() -> None:
    body = plan_adset_clone(_source(), target_pixel_id="TARGET_PIXEL", target_page_id="TARGET_PAGE")

    assert body["targeting"]["geo_locations"]["countries"] == ["GH"]
    assert body["daily_budget"] == "899"
    assert body["bid_strategy"] == "COST_CAP"


def test_clone_never_carries_the_source_identifiers() -> None:
    """ID исходной группы в теле создания означал бы перезапись оригинала —
    ровно та ошибка, которой славится Excel-импорт с незачищенными ID."""
    body = plan_adset_clone(_source(), target_pixel_id="TARGET_PIXEL", target_page_id="TARGET_PAGE")

    assert "id" not in body


def test_clone_remaps_account_scoped_assets() -> None:
    """Пиксель и страница принадлежат кабинету: перенесённый как есть пиксель
    молча собирал бы конверсии в чужой кабинет."""
    body = plan_adset_clone(_source(), target_pixel_id="TARGET_PIXEL", target_page_id="TARGET_PAGE")

    assert body["promoted_object"]["pixel_id"] == "TARGET_PIXEL"


def test_clone_refuses_custom_audiences_instead_of_dropping_them() -> None:
    """Кастомная аудитория целевому кабинету не принадлежит. Тихо выкинуть её
    значит собрать группу с другим таргетингом и не сказать об этом."""
    source = _source()
    source["targeting"]["custom_audiences"] = [{"id": "777", "name": "LAL 1%"}]

    with pytest.raises(ValueError, match="custom_audiences"):
        plan_adset_clone(source, target_pixel_id="TARGET_PIXEL", target_page_id="TARGET_PAGE")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_adset_clone.py -q`
Expected: FAIL — модуля `scripts/adset_clone.py` нет.

- [ ] **Step 3: Реализовать чистую функцию плана**

Создай `scripts/adset_clone.py`:

```python
"""План клонирования группы объявлений в другой кабинет.

Excel-импорт умеет то же самое, но у него две ловушки: незачищенный ID
превращает создание в перезапись оригинала, а пиксель, страница и кастомные
аудитории принадлежат кабинету и не переносятся. Здесь обе названы явно.
"""

from __future__ import annotations

# Поля группы, которые переносятся как есть. Остальное либо принадлежит
# кабинету, либо назначается Meta при создании.
_PORTABLE_FIELDS = (
    "name",
    "optimization_goal",
    "billing_event",
    "daily_budget",
    "lifetime_budget",
    "bid_strategy",
    "bid_amount",
    "attribution_spec",
    "destination_type",
    "start_time",
    "end_time",
)

_ACCOUNT_SCOPED_TARGETING = ("custom_audiences", "excluded_custom_audiences")


def plan_adset_clone(source: dict, *, target_pixel_id: str, target_page_id: str) -> dict:
    targeting = dict(source.get("targeting") or {})
    present = [key for key in _ACCOUNT_SCOPED_TARGETING if targeting.get(key)]
    if present:
        raise ValueError(
            "исходная группа опирается на аудитории целевому кабинету не принадлежащие: "
            + ", ".join(present)
        )

    body: dict = {key: source[key] for key in _PORTABLE_FIELDS if source.get(key) is not None}
    body["targeting"] = targeting
    promoted = dict(source.get("promoted_object") or {})
    if promoted:
        promoted["pixel_id"] = target_pixel_id
        if promoted.get("page_id"):
            promoted["page_id"] = target_page_id
        body["promoted_object"] = promoted
    return body
```

- [ ] **Step 4: Прогнать тест**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_adset_clone.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add scripts/adset_clone.py tests/unit/test_adset_clone.py
git commit -m "feat(scripts): план переноса группы объявлений в другой кабинет"
```

---

## Что требуется от тебя

### 1. Живой кабинет — есть, вопрос закрыт

Владелец завёл два новых кабинета 17.08: `2108857220005012` и `3570379159805007`. Первый
проверен — ограничений нет, мастер создания открывается. Треки A и C проверять есть где.

Старый `A-8186` (`1386439193678186`) остаётся ограниченным: «Требуется подтверждение», кнопка
«Создать» погашена. Для чтения истории это не помеха, для залива — помеха.

### 2. Подсказки ⓘ — получены, вопрос закрыт

Ничего не требуется — владелец прислал текст подсказок 17.08, они зафиксированы дословно:

> **Коэффициент захвата внимания.** Процент, сколько раз ваше видео было воспроизведено в
> течение как минимум 3 секунд. Эта метрика находится in development.

> **Коэффициент удержания.** Процент, сколько раз ваше видео было воспроизведено до конца
> после воспроизведения в течение как минимум 3 секунд. Эта метрика находится in development.

Отсюда два следствия, уже учтённых в треке B. Первое: удержание считается **от трёхсекундных
воспроизведений**, а числителем идут **досмотры до конца**, а не ThruPlay — для ролика длиннее
15 секунд это разные вещи, и ThruPlay дал бы завышенную цифру. Второе: обе метрики
«in development», поэтому колонка может пропасть или переименоваться — её отсутствие обязано
давать `unknown`, а не ронять скан.

### 3. Список кабинетов для сводки по креативам

Известны четыре: `996458953428822` (из ссылки на Insights), `1386439193678186` (A-8186,
ограничен), `2108857220005012` и `3570379159805007` (новые, 17.08). `cabinet_runtime` в базе
пуст — система не знает ни одного, потому что сканирование выключено.

Сводка «креатив × кабинет» из B5 имеет смысл только по полному списку. Подтверди, что в неё
входят все четыре, или назови подмножество. Заблокированность не мешает: исторические данные
читаются и на ограниченном кабинете — замер 315 / 43% / 1.48% снят как раз с такого.

### 4. Второй кабинет для трека C

Для переноса нужен целевой кабинет: его ID, ID пикселя и ID страницы. Без них трек C
останется чистой функцией с тестами и не будет проверен на живых данных.

### 5. Ссылка на тот блог

Ты спрашивал про него — у меня его нет, в репозитории тоже. Если там есть что-то про
актуальность настроек, пришли ссылку: возможно, часть выводов этого плана придётся поправить.
