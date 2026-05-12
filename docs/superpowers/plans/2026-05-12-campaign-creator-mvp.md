# Campaign Creator MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Полная автоматизация создания кампании в Facebook Ads Manager на базе записанной сессии (472 событий, KE_CR2). Расширить Offer пятью полями, добавить форму автосоздания, реализовать backend-степы с humanizer, остановка на «Сохранить как черновик».

**Architecture:** Backend — последовательность шагов в `core/campaign_creator/steps/`, обёрнутых humanizer'ом (random delays 80–300 ms, мышиный jitter, посимвольный ввод 40–110 ms). Селекторы — `data-auto-logging-id` из записи. Без checkpoint'ов — full autopilot до «Сохранить как черновик». Frontend — форма на ScriptsPage с блоками 1–8.

**Tech Stack:** FastAPI async, SQLAlchemy 2.x async, Alembic, Playwright async + CDP, Pydantic v2, React 19 + Vite.

---

## Константы (вшиты в код, не редактируются)

- `OPERATOR_INITIALS = "MV"`
- `CURRENCY = "USD"`
- `OBJECTIVE = "Продажи"`
- `EVENT = "Покупка"`
- `CTA = "Играть"`
- `ALWAYS_ADD_ANTARCTICA = True`
- URL-шаблон tracking-параметров:
  `sub2=MV&sub3={ad_name}&sub4={cabinet_id}&sub5={{campaign.name}}&sub6={{adset.name}}&sub7={{ad.name}}`

## Селекторы (из записи KE_CR2)

Все ключевые `data-auto-logging-id`:

| Поле | data-auto-logging-id |
|---|---|
| Название кампании | `fc22fd92f` |
| Дневной бюджет | `f4dd1c204` |
| Название адсета | `f6998cf82` |
| Pixel selector | `fbcbdc33b` |
| Geo search | `fa15127b5` |
| Название объявления | `f5fa3b7ca` |
| Заголовок | `fc2a3b7c8` |
| Landing URL | `f47cca745` |
| URL parameters | `f581d319d` |

(Полный маппинг — в `core/campaign_creator/selectors.py`, Task 7.)

---

## Task 1: Alembic-миграция — 5 новых полей в offers

**Files:**
- Create: `migrations/versions/<auto>_offer_creator_fields.py`

- [ ] **Step 1: Сгенерировать миграцию**

```bash
alembic revision -m "offer creator fields: landing_url, cabinet_id, pixel_id, geo_code, geo_slot_name"
```

- [ ] **Step 2: Написать upgrade/downgrade**

```python
def upgrade():
    op.add_column("offers", sa.Column("landing_url", sa.String(512), nullable=True))
    op.add_column("offers", sa.Column("cabinet_id", sa.String(64), nullable=True))
    op.add_column("offers", sa.Column("pixel_id", sa.String(64), nullable=True))
    op.add_column("offers", sa.Column("geo_code", sa.String(2), nullable=True))
    op.add_column("offers", sa.Column("geo_slot_name", sa.String(100), nullable=True))

def downgrade():
    for col in ("geo_slot_name", "geo_code", "pixel_id", "cabinet_id", "landing_url"):
        op.drop_column("offers", col)
```

- [ ] **Step 3: Применить**

```bash
alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/
git commit -m "feat(db): add 5 creator fields to offers"
```

---

## Task 2: Расширить ORM-модель Offer

**Files:**
- Modify: `core/models/__init__.py` (класс Offer ~ line 180)

- [ ] **Step 1: Добавить колонки**

```python
landing_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
cabinet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
pixel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
geo_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
geo_slot_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- [ ] **Step 2: Commit**

```bash
git add core/models/__init__.py
git commit -m "feat(models): expose creator fields on Offer"
```

---

## Task 3: Pydantic-схемы Offer

**Files:**
- Modify: `apps/api/schemas.py` (OfferCreate/Update/Read)

- [ ] **Step 1: Добавить optional поля во все три схемы**

```python
landing_url: str | None = None
cabinet_id: str | None = None
pixel_id: str | None = None
geo_code: str | None = Field(default=None, max_length=2)
geo_slot_name: str | None = None
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/schemas.py
git commit -m "feat(api): offer schemas include creator fields"
```

---

## Task 4: API роутер offers — пропуск новых полей

**Files:**
- Modify: `apps/api/routers/offers.py`

- [ ] **Step 1: Убедиться, что create/update пробрасывают новые поля**

Если используется `model.update(**body.model_dump(exclude_unset=True))` — менять ничего не нужно. Если поля перечислены явно — добавить новые.

- [ ] **Step 2: Тест**

```python
def test_offer_create_with_creator_fields(client):
    r = client.post("/api/offers", json={
        "code": "TEST_CR2", "cpa_amount": "10.00",
        "landing_url": "https://x.com", "cabinet_id": "act_1",
        "pixel_id": "12345", "geo_code": "KE", "geo_slot_name": "Кения"
    })
    assert r.status_code == 201
    assert r.json()["geo_code"] == "KE"
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/api/test_offers.py -x
git add apps/api tests/api
git commit -m "feat(api): persist creator fields on offer"
```

---

## Task 5: Frontend форма Offer

**Files:**
- Modify: `frontend/src/pages/OffersPage.jsx`

- [ ] **Step 1: Добавить пять инпутов в форму**

Поля: Landing URL, Cabinet ID, Pixel ID, Geo code (2 буквы), Geo slot name. Подписи на русском.

- [ ] **Step 2: Прокинуть в POST/PUT тело**

- [ ] **Step 3: Проверить вручную через UI + commit**

```bash
git add frontend/src/pages/OffersPage.jsx
git commit -m "feat(ui): offer form fields for campaign creator"
```

---

## Task 6: Расширить StepContext

**Files:**
- Modify: `core/campaign_creator/steps/base.py`

- [ ] **Step 1: Добавить поля в dataclass**

```python
@dataclass
class StepContext:
    offer_code: str
    cabinet_id: str
    campaign_name: str
    pixel_id: str
    landing_url: str
    geo_code: str
    geo_slot_name: str
    daily_budget: float
    attribution_days: int  # 1 или 7
    budget_level: str  # "CBO" | "ABO"
    iter_num: int
    adsets: list[AdsetSpec]  # name, headline, primary_text, creo_subfolder
    creo_folder: str
    extra: dict = field(default_factory=dict)
```

```python
@dataclass
class AdsetSpec:
    name: str
    headline: str
    primary_text: str
    creo_subfolder: str
```

- [ ] **Step 2: Тест dataclass'ов**

```python
def test_stepcontext_minimal():
    ctx = StepContext(offer_code="X", cabinet_id="a", campaign_name="c",
                      pixel_id="p", landing_url="https://x", geo_code="KE",
                      geo_slot_name="Кения", daily_budget=20.0,
                      attribution_days=7, budget_level="CBO", iter_num=1,
                      adsets=[], creo_folder="/tmp/x")
    assert ctx.budget_level == "CBO"
```

- [ ] **Step 3: Commit**

```bash
git add core/campaign_creator/steps/base.py tests/
git commit -m "feat(creator): extend StepContext with full param set"
```

---

## Task 7: Маппинг селекторов

**Files:**
- Create: `core/campaign_creator/selectors.py`

- [ ] **Step 1: Положить все data-auto-logging-id**

```python
SELECTORS = {
    "campaign_name": '[data-auto-logging-id="fc22fd92f"]',
    "daily_budget": '[data-auto-logging-id="f4dd1c204"]',
    "adset_name": '[data-auto-logging-id="f6998cf82"]',
    "pixel": '[data-auto-logging-id="fbcbdc33b"]',
    "geo_search": '[data-auto-logging-id="fa15127b5"]',
    "ad_name": '[data-auto-logging-id="f5fa3b7ca"]',
    "headline": '[data-auto-logging-id="fc2a3b7c8"]',
    "landing_url": '[data-auto-logging-id="f47cca745"]',
    "url_params": '[data-auto-logging-id="f581d319d"]',
}
```

- [ ] **Step 2: Commit**

```bash
git add core/campaign_creator/selectors.py
git commit -m "feat(creator): centralize ads-manager selectors"
```

---

## Task 8: Humanizer

**Files:**
- Create: `core/campaign_creator/humanizer.py`
- Test: `tests/unit/test_humanizer.py`

- [ ] **Step 1: Тест диапазонов**

```python
import asyncio
from core.campaign_creator.humanizer import human_wait, _rand_delay

def test_rand_delay_in_range():
    for _ in range(100):
        d = _rand_delay(80, 300)
        assert 80 <= d <= 300
```

- [ ] **Step 2: Реализация**

```python
import asyncio, random

def _rand_delay(lo_ms: int, hi_ms: int) -> int:
    return random.randint(lo_ms, hi_ms)

async def human_wait(lo_ms=80, hi_ms=300):
    await asyncio.sleep(_rand_delay(lo_ms, hi_ms) / 1000)

async def human_click(page, selector):
    el = await page.wait_for_selector(selector)
    box = await el.bounding_box()
    jx = random.uniform(-3, 3); jy = random.uniform(-3, 3)
    await page.mouse.move(box["x"] + box["width"]/2 + jx,
                          box["y"] + box["height"]/2 + jy,
                          steps=random.randint(8, 20))
    await human_wait(40, 120)
    await page.mouse.down(); await human_wait(30, 80); await page.mouse.up()
    await human_wait(120, 280)

async def human_type(page, selector, text: str):
    await human_click(page, selector)
    for ch in text:
        await page.keyboard.type(ch)
        await human_wait(40, 110)

async def human_select(page, combobox_sel, option_text: str):
    await human_click(page, combobox_sel)
    await human_wait(200, 450)
    await page.click(f'[role="option"]:has-text("{option_text}")')
    await human_wait(150, 350)

async def human_scroll(page, dy: int):
    await page.mouse.wheel(0, dy + random.randint(-20, 20))
    await human_wait(200, 500)
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/unit/test_humanizer.py -x
git add core/campaign_creator/humanizer.py tests/unit/test_humanizer.py
git commit -m "feat(creator): humanizer with randomized delays + mouse jitter"
```

---

## Task 9: Step create_campaign (рефактор)

**Files:**
- Modify: `core/campaign_creator/steps/create_campaign.py`

- [ ] **Step 1: Реализация через humanizer + selectors**

```python
from core.campaign_creator.selectors import SELECTORS
from core.campaign_creator.humanizer import human_click, human_type, human_wait

class CreateCampaignStep(BaseStep):
    name = "create_campaign"
    async def execute(self, page, ctx):
        await human_click(page, '[aria-label="Создать"]')
        await human_wait(400, 800)
        await human_click(page, '[aria-label="Продажи"]')
        await human_click(page, '[aria-label="Продолжить"]')
        await human_type(page, SELECTORS["campaign_name"], ctx.campaign_name)
        return StepResult(success=True, message="кампания создана")
```

- [ ] **Step 2: Commit**

```bash
git add core/campaign_creator/steps/create_campaign.py
git commit -m "feat(creator): real selectors for create_campaign"
```

---

## Task 10: Новые степы

**Files (создать каждый):**
- `core/campaign_creator/steps/set_budget.py` — CBO/ABO + сумма
- `core/campaign_creator/steps/set_attribution.py` — 1d/7d
- `core/campaign_creator/steps/set_pixel_event.py` — Pixel + событие «Покупка»
- `core/campaign_creator/steps/set_geo.py` — рефактор: geo_code + Антарктида
- `core/campaign_creator/steps/create_adset.py` — итерация по ctx.adsets, имя
- `core/campaign_creator/steps/upload_creatives.py` — файлы из creo_folder/subfolder
- `core/campaign_creator/steps/fill_texts.py` — headline + primary_text per ad
- `core/campaign_creator/steps/set_cta.py` — «Играть»
- `core/campaign_creator/steps/set_tracking_url.py` — landing_url + url_params
- `core/campaign_creator/steps/save_draft.py` — клик «Сохранить как черновик», стоп

Каждый степ:
- [ ] **Step A:** прописать селекторы из `SELECTORS` + humanizer
- [ ] **Step B:** мини-тест на сборку URL/имени, где применимо
- [ ] **Step C:** commit `feat(creator): step <name>`

Ключевая логика `set_tracking_url`:

```python
url = ctx.landing_url
params = (f"sub2=MV&sub3={{ad_name}}&sub4={ctx.cabinet_id}"
          "&sub5={{campaign.name}}&sub6={{adset.name}}&sub7={{ad.name}}")
await human_type(page, SELECTORS["landing_url"], url)
await human_type(page, SELECTORS["url_params"], params)
```

Ключевая логика `set_geo`:

```python
await human_type(page, SELECTORS["geo_search"], ctx.geo_slot_name)
await human_wait(500, 900)
await page.click(f'[role="option"]:has-text("{ctx.geo_slot_name}")')
await human_type(page, SELECTORS["geo_search"], "Антарктида")
await human_wait(500, 900)
await page.click('[role="option"]:has-text("Антарктида")')
```

---

## Task 11: Runner — убрать checkpoint-паузы

**Files:**
- Modify: `core/campaign_creator/runner.py`

- [ ] **Step 1: Заменить run_until_checkpoint на run_all**

```python
async def run_all(self, page, ctx):
    for step in self._steps:
        await self._set_status(step.name, "running")
        result = await step.execute(page, ctx)
        if not result.success:
            await self._set_status(step.name, "failed", error=result.message)
            raise RuntimeError(result.message)
        await self._set_status(step.name, "done")
```

Удалить checkpoint-логику полностью.

- [ ] **Step 2: Commit**

```bash
git add core/campaign_creator/runner.py
git commit -m "refactor(creator): full autopilot, drop checkpoints"
```

---

## Task 12: API — расширенный POST /start

**Files:**
- Modify: `apps/api/routers/campaign_creator.py`
- Modify: `apps/api/schemas.py` (CampaignCreatorStartRequest)

- [ ] **Step 1: Расширить схему запроса**

```python
class AdsetIn(BaseModel):
    name: str
    headline: str
    primary_text: str
    creo_subfolder: str

class CampaignCreatorStartRequest(BaseModel):
    offer_code: str
    iter_num: int
    daily_budget: float
    budget_level: Literal["CBO", "ABO"]
    attribution_days: Literal[1, 7]
    creo_folder: str
    adsets: list[AdsetIn]
```

- [ ] **Step 2: В роутере подтянуть Offer и собрать StepContext**

```python
offer = await session.scalar(select(Offer).where(Offer.code == body.offer_code))
ctx = StepContext(
    offer_code=offer.code, cabinet_id=offer.cabinet_id,
    campaign_name=f"CR{body.iter_num} | {offer.code} | MV | {date_str}",
    pixel_id=offer.pixel_id, landing_url=offer.landing_url,
    geo_code=offer.geo_code, geo_slot_name=offer.geo_slot_name,
    daily_budget=body.daily_budget, attribution_days=body.attribution_days,
    budget_level=body.budget_level, iter_num=body.iter_num,
    adsets=[AdsetSpec(**a.model_dump()) for a in body.adsets],
    creo_folder=body.creo_folder,
)
```

- [ ] **Step 3: Тест генерации campaign_name**

```python
def test_campaign_name_format():
    name = build_campaign_name(iter_num=2, offer_code="KE_CR", date="25.03")
    assert name == "CR2 | KE_CR | MV | 25.03"
```

- [ ] **Step 4: Commit**

```bash
git add apps/api tests/api
git commit -m "feat(api): full campaign-creator start payload"
```

---

## Task 13: Frontend — форма автосоздания

**Files:**
- Modify: `frontend/src/pages/ScriptsPage.jsx` (или создать `CampaignCreatorPage.jsx`)

Блоки формы (все на русском, RU locale):

1. Выбор оффера (select из `/api/offers`)
2. Номер итерации (number, default 1)
3. Дневной бюджет USD (number)
4. Уровень бюджета: радио CBO / ABO
5. Атрибуция: радио 1d / 7d
6. Папка с креативами (text — путь, или file picker)
7. Адсеты — динамический список:
   - Имя адсета
   - Заголовок
   - Основной текст
   - Подпапка с креативами (из creo_folder)
8. Кнопка «Запустить» → POST `/api/campaign-creator/start`

- [ ] **Step 1: Компонент формы**
- [ ] **Step 2: Превью структуры папки**

После ввода creo_folder делать GET (или клиентский FS-read) — показать список подпапок/файлов.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages
git commit -m "feat(ui): campaign creator autopilot form"
```

---

## Task 14: Тесты

**Files:**
- Create: `tests/unit/test_humanizer.py` (см. Task 8)
- Create: `tests/unit/test_url_template.py`
- Create: `tests/unit/test_campaign_name.py`
- Create: `tests/integration/test_creator_runner_mock.py`

- [ ] **Step 1: URL-шаблон**

```python
def test_url_params_template():
    out = build_url_params(cabinet_id="act_999")
    assert "sub2=MV" in out
    assert "sub4=act_999" in out
    assert "sub5={{campaign.name}}" in out
```

- [ ] **Step 2: campaign_name**

См. Task 12 Step 3.

- [ ] **Step 3: Runner на моках Playwright**

```python
async def test_runner_executes_all_steps(monkeypatch):
    page = MagicMock()
    page.wait_for_selector = AsyncMock(return_value=MagicMock(
        bounding_box=AsyncMock(return_value={"x":0,"y":0,"width":10,"height":10})))
    runner = CampaignCreatorRunner(steps=[FakeStep()])
    await runner.run_all(page, fake_ctx())
    assert FakeStep.called
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/ -x
git add tests/
git commit -m "test(creator): humanizer, url, name, runner"
```

---

## Self-review checklist

- [x] 5 полей Offer покрыты: landing_url, cabinet_id, pixel_id, geo_code, geo_slot_name
- [x] Константы вшиты: MV, USD, Продажи, Покупка, Играть, Антарктида
- [x] URL-шаблон корректный
- [x] Селекторы из реальной записи (KE_CR2)
- [x] Humanizer диапазоны: 80–300 ms клик, 40–110 ms ввод, jitter ±3 px
- [x] Без checkpoint'ов — full autopilot до «Сохранить как черновик»
- [x] Каждый степ — один файл, один селектор-пакет, тесты на критичной логике
