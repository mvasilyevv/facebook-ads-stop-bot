# Смешанные медиа + per-offer нумерация креативов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разрешить фото+видео в одном adset (тип per-concept по расширению) и дать сквозную per-offer нумерацию кодов креативов с реестром выданных кодов.

**Architecture:** `media_kind` уже живёт на каждом `UniquifiedAd` и `execute.py` выбирает creative по нему — переключаем источник с `block.kind` на расширение файла концепта и убираем `kind` с кампании. Код креатива `{offer}_CR{NNN}` уже не зависит от имени файла; добавляем `CampaignConfig.code_start` (база сквозной нумерации), атомарный per-offer аллокатор (`offer_creative_seq`) на launch и реестр созданных кодов (`campaign_creative`).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, FastAPI, Pydantic v2, Alembic, pytest; React 19 + TS strict + vitest (web `frontend/` и mini `frontend-mini/`).

## Global Constraints

- **НЕ запускать `pytest tests/integration` и любые тесты на живой БД `:5433`** — они стирают `offers`/`offer_rules`. Локально только: `ruff check .`, `pytest tests/unit`, `pnpm --filter <pkg> exec vitest run`, `pnpm --filter <pkg> exec tsc --noEmit`, `pnpm --filter <pkg> lint`. Integration-тесты пишем, но они гоняются на изолированной БД в **CI** (push → GitHub Actions).
- **Money-safety:** не менять `builder.py` `promoted_object`/`billing`; preview==launch==retry (коды детерминированы и зафиксированы в `run.config` при создании run'а); идемпотентность через `ON CONFLICT`.
- **Ruff:** line-length=100, target py312, правила E/F/I/B/ASYNC.
- **Комментарии/сообщения — по-русски.** Над каждым тестом — короткий русский комментарий со сценарием.
- **Файлы нового кода — ≤500 строк.**
- Alembic head сейчас `0027_drop_offer_default_page_id`; новая миграция `0028`, линейный down_revision.
- Коммит на завершении каждой задачи (main, по-русски, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`, только свои файлы). Push — после явного OK пользователя.

---

## Файловая карта

**Backend**
- `core/campaign_builder/config.py` — убрать `CampaignBlock.kind`; валидатор reject неизвестного расширения; добавить `CampaignConfig.code_start`.
- `core/campaign_builder/uniquify.py` — `media_kind` per-concept; диспатч PIL/ffmpeg по `concept.kind`.
- `core/campaign_builder/builder.py` — убрать `kind` из `CampaignSpec_Block`/`_build_block`; `build_campaign_spec` стартует с `cfg.code_start`; helper `total_code_span(cfg)`.
- `core/campaign_builder/execute.py` — стартовать с `cfg.code_start`; callback `on_creative_created`.
- `core/campaign_builder/creative_ledger.py` (новый) — чистые SQL-функции аллокатора и реестра.
- `core/models/campaigns/creative_seq.py` (новый) — ORM `OfferCreativeSeq`, `CampaignCreative`.
- `core/models/campaigns/__init__.py` — экспорт новых моделей.
- `migrations/versions/0028_creative_seq_ledger.py` (новый) — таблицы + backfill.
- `apps/api/routers/v1/campaigns_create.py` — peek в validate; аллокация в launch.
- `apps/campaign_creator_worker/__init__.py` — убрать `or block.kind == "video"`; передать `on_creative_created`, пишущий в реестр с `run_id`.

**Frontend web (`frontend/`)**
- `src/lib/api/campaigns.ts` — убрать `kind` из `CampaignStructure`; в `ValidatePlanOut` campaign — `kind` опционально/убрать.
- `src/stores/campaignWizard.ts` — `buildConfig` без `refKind === block.kind`.
- `src/components/domain/campaigns/WizardStep4Structure.tsx` — одна кнопка «+ Кампания».
- `src/components/domain/campaigns/WizardStep5Creatives.tsx` — привязка без kind-фильтра; mixed-счётчики.
- `src/components/domain/campaigns/WizardStep6Preview.tsx` — убрать `kind`-токен; empty-block по `concept_refs`.

**Frontend mini (`frontend-mini/`)**
- `src/routes/campaigns/-wizardStore.ts` — тип `CampaignStructure` без `kind`.
- `src/routes/campaigns/StepStructure.tsx` — убрать Select типа, один add.
- `src/routes/campaigns/StepCreatives.tsx` — `concept_refs` = все концепты (без kind-фильтра).

---

## ФАЗА 1 — Backend: смешанные медиа (тип per-concept)

### Task 1: `config.py` — убрать `kind` с кампании, reject неизвестного расширения

**Files:**
- Modify: `core/campaign_builder/config.py` (`CampaignBlock`, ~168-197)
- Test: `tests/unit/test_campaign_config.py` (создать/дополнить)

**Interfaces:**
- Produces: `CampaignBlock` без поля `kind`; валидатор `_check` отклоняет `concept_refs` с `ref_media_kind(ref) is None`.

- [ ] **Step 1: Тест — смешанный блок валиден, неизвестное расширение нет**

```python
# Смешанный блок (фото+видео) проходит валидацию; неизвестное расширение — ValueError.
import pytest
from core.campaign_builder.config import CampaignBlock, AdsetConfig

def _adset() -> AdsetConfig:
    return AdsetConfig(name="as", dir=".", glob="*")

def test_mixed_block_is_valid():
    block = CampaignBlock(
        key="c1", name="C1", adsets=[_adset()],
        concept_refs=["a.jpg", "b.mp4"],
    )
    assert block.concept_refs == ["a.jpg", "b.mp4"]

def test_unknown_extension_rejected():
    with pytest.raises(ValueError, match="неизвестн"):
        CampaignBlock(key="c1", name="C1", adsets=[_adset()], concept_refs=["a.txt"])
```

- [ ] **Step 2: Запустить — упадёт (поле `kind` ещё обязательно)**

Run: `pytest tests/unit/test_campaign_config.py -q`
Expected: FAIL (`kind` required / валидатор ещё про несовпадение типа).

- [ ] **Step 3: Реализация — убрать `kind`, переписать валидатор**

В `core/campaign_builder/config.py` заменить тело `CampaignBlock`:

```python
class CampaignBlock(BaseModel):
    """Одна кампания: список adset'ов + смешанный набор концептов (фото/видео).

    Тип каждого ad определяется по расширению файла концепта (ref_media_kind),
    не по кампании. concept_refs — единый источник концептов блока.
    """

    key: str
    name: str  # шаблон имени с плейсхолдерами
    adsets: list[AdsetConfig]
    concept_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> CampaignBlock:
        # Money-safety: уникализатор умеет только image (PIL) и video (ffmpeg).
        # Файл с неизвестным расширением уронил бы материализацию уже ПОСЛЕ
        # создания объектов в Meta → орфаны. Отклоняем ДО любого POST.
        for ref in self.concept_refs:
            if ref_media_kind(ref) is None:
                raise ValueError(
                    f"кампания {self.key!r}: концепт {ref!r} имеет неизвестное "
                    f"расширение — поддерживаются только фото и видео"
                )
        return self
```

- [ ] **Step 4: Запустить — зелёно**

Run: `pytest tests/unit/test_campaign_config.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_builder/config.py tests/unit/test_campaign_config.py
git commit -m "feat(campaign-builder): CampaignBlock без kind — смешанные медиа per-concept"
```

---

### Task 2: `uniquify.py` — `media_kind` по `concept.kind`

**Files:**
- Modify: `core/campaign_builder/uniquify.py` (`build_uniquification_plan` ~194; `uniquify_concepts` ~258-270)
- Test: `tests/unit/test_uniquify_plan.py` (создать/дополнить)

**Interfaces:**
- Consumes: `ConceptInput.kind` (уже есть), `CampaignBlock` без `kind` (Task 1).
- Produces: `UniquifiedAd.media_kind` равен `concept.kind`; `uniquify_concepts` диспатчит PIL/ffmpeg по `concept.kind`.

- [ ] **Step 1: Тест — план смешанного блока проставляет media_kind per-concept**

```python
# В одном блоке фото и видео → media_kind вариантов берётся из типа концепта.
from core.campaign_builder.config import CampaignBlock, AdsetConfig
from core.campaign_builder.uniquify import build_uniquification_plan, ConceptInput
from tests.unit._campaign_factories import make_config  # хелпер; см. ниже

def test_plan_media_kind_per_concept():
    block = CampaignBlock(
        key="c1", name="C1",
        adsets=[AdsetConfig(name="as1", dir=".", glob="*")],
        concept_refs=["a.jpg", "b.mp4"],
    )
    cfg = make_config(campaigns=[block])
    concepts = [
        ConceptInput(concept_id="c1:0:a", kind="image", content=b"x", filename="a.jpg"),
        ConceptInput(concept_id="c1:1:b", kind="video", path="/tmp/b.mp4", filename="b.mp4"),
    ]
    plan = build_uniquification_plan(cfg, block, concepts, copies=1)
    kinds = {ad.concept_id: ad.media_kind for ad in plan.adsets[0].ads}
    assert kinds["c1:0:a"] == "image"
    assert kinds["c1:1:b"] == "video"
```

> Если фабрики `tests/unit/_campaign_factories.py` нет — создать с функцией `make_config(**over)`, собирающей минимальный валидный `CampaignConfig` (Account/Budget COST_CAP+bid_amount/Targeting/destination_link/offer_code). Переиспользовать существующую фабрику из соседних unit-тестов, если она уже есть (grep `make_config`/`_config(` в `tests/unit`).

- [ ] **Step 2: Запустить — упадёт (media_kind берётся из block.kind, а его уже нет → AttributeError/неверное значение)**

Run: `pytest tests/unit/test_uniquify_plan.py -q`
Expected: FAIL.

- [ ] **Step 3: Реализация**

`build_uniquification_plan`: в цикле по концептам подставлять тип концепта. Заменить строку `media_kind=block.kind` — теперь `media_kind` берётся из `concept.kind`:

```python
    variants_by_concept: dict[str, list[UniquifiedAd]] = {}
    for c_index, concept in enumerate(concepts):
        variants: list[UniquifiedAd] = []
        for i in range(copies):
            variants.append(
                UniquifiedAd(
                    concept_id=concept.concept_id,
                    copy_index=i,
                    code=layout[i][c_index],
                    seed=_seed_text(cfg, concept.concept_id, i),
                    media_kind=concept.kind,
                )
            )
        variants_by_concept[concept.concept_id] = variants
```

`uniquify_concepts`: диспатч по `concept.kind` (а не `block.kind`):

```python
    by_id = _concept_by_id(concepts)
    for adset in plan.adsets:
        for ad in adset.ads:
            concept = by_id[ad.concept_id]
            if concept.kind == "video":
                ad.media_bytes = await _uniquify_one_video(concept, ad)
                ad.media_kind = "video"
            else:
                ad.media_bytes = await _uniquify_one_image(cfg, concept, ad)
                ad.media_kind = "image"
    return plan.adsets
```

- [ ] **Step 4: Запустить — зелёно (плюс существующие uniquify-тесты)**

Run: `pytest tests/unit/test_uniquify_plan.py tests/unit -k uniquif -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_builder/uniquify.py tests/unit/test_uniquify_plan.py tests/unit/_campaign_factories.py
git commit -m "feat(campaign-builder): media_kind per-concept в плане уникализации"
```

---

### Task 3: worker — убрать фолбэк `or block.kind == "video"`

**Files:**
- Modify: `apps/campaign_creator_worker/__init__.py` (~260)
- Test: покрыто существующими worker-тестами + Task 2 (логика типа уже по расширению).

- [ ] **Step 1: Реализация** — заменить строку определения типа:

```python
            is_video = path.suffix.lower() in _VIDEO_EXTS
```

(остальное тело `ConceptInput(...)` без изменений).

- [ ] **Step 2: Проверка — ruff + unit**

Run: `ruff check apps/campaign_creator_worker/ && pytest tests/unit -k campaign_creator -q`
Expected: PASS (тип теперь только по расширению; `block.kind` больше не существует — иначе AttributeError выявит остаточные ссылки).

- [ ] **Step 3: grep остаточных `block.kind` / `.kind` по билдеру и воркеру**

Run: `grep -rn "block.kind\|\.kind" core/campaign_builder apps/campaign_creator_worker`
Expected: только `concept.kind` / `ad.media_kind` / `ref_media_kind`; ни одного `block.kind`.

- [ ] **Step 4: Коммит**

```bash
git add apps/campaign_creator_worker/__init__.py
git commit -m "refactor(worker): тип концепта только по расширению (block.kind удалён)"
```

---

### Task 4: `builder.py` — убрать `kind` из `CampaignSpec_Block`

**Files:**
- Modify: `core/campaign_builder/builder.py` (`CampaignSpec_Block` dataclass; `_build_block` return ~270-277)
- Test: `tests/unit` существующие builder-тесты + новый на смешанный блок.

**Interfaces:**
- Produces: `CampaignSpec_Block` без атрибута `kind`.

- [ ] **Step 1: Тест — спека смешанного блока строится, ads с кодами по K концептов**

```python
# Смешанный блок (2 концепта × 2 adset) → 2 adset по 2 ad, коды сквозные.
from core.campaign_builder.config import CampaignBlock, AdsetConfig
from core.campaign_builder.builder import build_campaign_spec
from tests.unit._campaign_factories import make_config

def test_spec_mixed_block():
    block = CampaignBlock(
        key="c1", name="C1 {offer}",
        adsets=[AdsetConfig(name="as1 {offer}", dir=".", glob="*"),
                AdsetConfig(name="as2 {offer}", dir=".", glob="*")],
        concept_refs=["a.jpg", "b.mp4"],
    )
    cfg = make_config(campaigns=[block], offer_code="GH")
    spec = build_campaign_spec(cfg, concept_counts={"c1": 2})
    assert len(spec.campaigns[0].adsets) == 2
    assert all(len(a.ads) == 2 for a in spec.campaigns[0].adsets)
    assert not hasattr(spec.campaigns[0], "kind")
```

- [ ] **Step 2: Запустить — упадёт (`kind` ещё в dataclass)**

Run: `pytest tests/unit -k "spec_mixed_block" -q`
Expected: FAIL (`hasattr kind` True / поле обязательно).

- [ ] **Step 3: Реализация** — удалить поле `kind` из `CampaignSpec_Block` и `kind=block.kind` из конструктора в `_build_block` (return ~270-277):

```python
    return CampaignSpec_Block(
        key=block.key,
        name=camp_name,
        body=campaign_body(cfg, camp_name),
        status="PAUSED",
        adsets=adsets,
    )
```

И в самом dataclass `CampaignSpec_Block` убрать строку `kind: str`.

- [ ] **Step 4: Запустить — зелёно (все builder-тесты)**

Run: `pytest tests/unit -k "campaign and (spec or builder)" -q`
Expected: PASS. Если падают существующие тесты, читающие `.kind` — обновить их (kind больше не часть контракта спеки).

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_builder/builder.py tests/unit/
git commit -m "refactor(campaign-builder): CampaignSpec_Block без kind"
```

---

## ФАЗА 2 — Backend: per-offer нумерация + реестр

### Task 5: `CampaignConfig.code_start` + использование в builder/execute

**Files:**
- Modify: `core/campaign_builder/config.py` (`CampaignConfig`)
- Modify: `core/campaign_builder/builder.py` (`build_campaign_spec` ~308; `total_code_span` новый)
- Modify: `core/campaign_builder/execute.py` (~386)
- Test: `tests/unit/test_code_start.py` (создать)

**Interfaces:**
- Produces: `CampaignConfig.code_start: int = 1`; `build_campaign_spec` и `execute_campaign_spec` стартуют нумерацию с `cfg.code_start`; `total_code_span(cfg: CampaignConfig) -> int`.

- [ ] **Step 1: Тест — code_start сдвигает коды; total_code_span = Σ K×N**

```python
# code_start=10 → первый код CR010; total_code_span = сумма K×N по блокам.
from core.campaign_builder.config import CampaignBlock, AdsetConfig
from core.campaign_builder.builder import build_campaign_spec, total_code_span
from tests.unit._campaign_factories import make_config

def _block(key, n_adsets, refs):
    return CampaignBlock(
        key=key, name=f"{key} {{offer}}",
        adsets=[AdsetConfig(name=f"as{i} {{offer}}", dir=".", glob="*") for i in range(n_adsets)],
        concept_refs=refs,
    )

def test_code_start_offsets_codes():
    cfg = make_config(campaigns=[_block("c1", 1, ["a.jpg"])], offer_code="GH", code_start=10)
    spec = build_campaign_spec(cfg, concept_counts={"c1": 1})
    assert spec.campaigns[0].adsets[0].ads[0].code == "GH_CR010"

def test_total_code_span():
    cfg = make_config(
        campaigns=[_block("c1", 2, ["a.jpg", "b.mp4"]), _block("c2", 3, ["c.jpg"])],
        offer_code="GH",
    )
    # c1: 2 концепта × 2 adset = 4; c2: 1 × 3 = 3 → 7
    assert total_code_span(cfg) == 7
```

- [ ] **Step 2: Запустить — упадёт (нет `code_start`/`total_code_span`)**

Run: `pytest tests/unit/test_code_start.py -q`
Expected: FAIL.

- [ ] **Step 3: Реализация**

В `CampaignConfig` (config.py) добавить поле (рядом с прочими, после `creative_prefix`):

```python
    # База сквозной нумерации кодов креативов. =1 по умолчанию; на launch
    # аллокатор per-offer проставляет реальное смещение (см. campaigns_create.launch).
    code_start: int = 1
```

В `build_campaign_spec` (builder.py) заменить инициализацию:

```python
    code_start = cfg.code_start  # база сквозной нумерации (per-offer на launch)
```

В `execute_campaign_spec` (execute.py ~386) заменить:

```python
    code_start = cfg.code_start
```

Добавить в builder.py чистую функцию:

```python
def total_code_span(cfg: CampaignConfig) -> int:
    """Сколько кодов CRxxx займёт весь залив: Σ (len(concept_refs) × len(adsets)) по блокам.

    Используется аллокатором per-offer (campaigns_create.launch) для резерва диапазона.
    """
    return sum(
        block_code_span(len(b.concept_refs), len(b.adsets)) for b in cfg.campaigns
    )
```

- [ ] **Step 4: Запустить — зелёно + регресс существующих**

Run: `pytest tests/unit/test_code_start.py tests/unit -k "campaign and code" -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_builder/config.py core/campaign_builder/builder.py core/campaign_builder/execute.py tests/unit/test_code_start.py
git commit -m "feat(campaign-builder): CampaignConfig.code_start + total_code_span"
```

---

### Task 6: ORM-модели + миграция 0028 (offer_creative_seq, campaign_creative) + backfill

**Files:**
- Create: `core/models/campaigns/creative_seq.py`
- Modify: `core/models/campaigns/__init__.py`
- Create: `migrations/versions/0028_creative_seq_ledger.py`
- Test: `tests/integration/test_creative_seq_migration.py` (создать; **CI-only**)

**Interfaces:**
- Produces: таблицы `offer_creative_seq(offer_code PK, next_seq)`, `campaign_creative(id, offer_code, code, kind, meta_creative_id, run_id, created_at, UNIQUE(offer_code, code))`; ORM `OfferCreativeSeq`, `CampaignCreative`.

- [ ] **Step 1: ORM-модели** — `core/models/campaigns/creative_seq.py`:

```python
"""ORM: per-offer счётчик кодов креативов + реестр выданных кодов.

offer_creative_seq — атомарный аллокатор сквозной нумерации OFFER_CRxxx (см.
core/campaign_builder/creative_ledger.py). campaign_creative — append-only реестр
созданных в Meta креативов (какие коды/creative_id залиты по офферу).
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly


class OfferCreativeSeq(Base):
    """High-water-mark номера кода креатива по офферу. next_seq = последний выданный."""

    __tablename__ = "offer_creative_seq"

    offer_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class CampaignCreative(CreatedAtOnly, Base):
    """Реестр созданных креативов (append-only). UNIQUE(offer_code, code) → идемпотентность."""

    __tablename__ = "campaign_creative"
    __table_args__ = (UniqueConstraint("offer_code", "code", name="uq_campaign_creative_offer_code"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    offer_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    meta_creative_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

> Сверить импорт `CreatedAtOnly` и наличие `gen_random_uuid()` с другими моделями (`core/models/campaigns/run.py`); если в проекте UUID PK генерится иначе — повторить тот же паттерн.

- [ ] **Step 2: Экспорт** — в `core/models/campaigns/__init__.py` добавить:

```python
from core.models.campaigns.creative_seq import CampaignCreative, OfferCreativeSeq
```

и в `__all__` (если он есть) — `"OfferCreativeSeq"`, `"CampaignCreative"`.

- [ ] **Step 3: Миграция** — `migrations/versions/0028_creative_seq_ledger.py`:

```python
"""offer_creative_seq + campaign_creative (per-offer нумерация кодов + реестр).

Revision ID: 0028_creative_seq_ledger
Revises: 0027_drop_offer_default_page_id
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_creative_seq_ledger"
down_revision = "0027_drop_offer_default_page_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offer_creative_seq",
        sa.Column("offer_code", sa.String(64), primary_key=True),
        sa.Column("next_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "campaign_creative",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("offer_code", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("meta_creative_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("offer_code", "code", name="uq_campaign_creative_offer_code"),
    )
    op.create_index("ix_campaign_creative_offer_code", "campaign_creative", ["offer_code"])

    # Backfill: чтобы новые коды не наехали на коды старых заливов, ставим next_seq =
    # суммарному числу уже созданных креативов по офферу (из campaign_run.created_meta_ids).
    op.execute(
        """
        INSERT INTO offer_creative_seq (offer_code, next_seq)
        SELECT config->>'offer_code' AS offer_code,
               SUM(COALESCE(jsonb_array_length(created_meta_ids->'creatives'), 0)) AS next_seq
        FROM campaign_run
        WHERE config->>'offer_code' IS NOT NULL
          AND created_meta_ids ? 'creatives'
        GROUP BY config->>'offer_code'
        ON CONFLICT (offer_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_creative_offer_code", table_name="campaign_creative")
    op.drop_table("campaign_creative")
    op.drop_table("offer_creative_seq")
```

- [ ] **Step 4: Integration-тест (CI-only)** — `tests/integration/test_creative_seq_migration.py`:

```python
# После миграции таблицы существуют и backfill агрегирует число креативов по офферу.
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

async def test_tables_exist(engine):
    async with engine.connect() as conn:
        for t in ("offer_creative_seq", "campaign_creative"):
            ok = (await conn.execute(
                text("SELECT to_regclass(:t)"), {"t": t}
            )).scalar()
            assert ok is not None, f"таблица {t} не создана"
```

> Использовать существующую фикстуру `engine` из `tests/integration/conftest.py`. **Локально не запускать** — гоняется в CI.

- [ ] **Step 5: Проверка локально (статика, без БД)**

Run: `ruff check core/models/campaigns/ migrations/versions/0028_creative_seq_ledger.py && python -c "import migrations.versions.0028_creative_seq_ledger" 2>/dev/null || python -c "import importlib.util,sys; spec=importlib.util.spec_from_file_location('m','migrations/versions/0028_creative_seq_ledger.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('revision', m.revision, 'down', m.down_revision)"`
Expected: ruff clean; печатает `revision 0028_creative_seq_ledger down 0027_drop_offer_default_page_id`.

- [ ] **Step 6: Коммит**

```bash
git add core/models/campaigns/ migrations/versions/0028_creative_seq_ledger.py tests/integration/test_creative_seq_migration.py
git commit -m "feat(db): offer_creative_seq + campaign_creative (миграция 0028 + backfill)"
```

---

### Task 7: `creative_ledger.py` — аллокатор + запись реестра (чистые SQL-функции)

**Files:**
- Create: `core/campaign_builder/creative_ledger.py`
- Test: `tests/integration/test_creative_ledger.py` (создать; **CI-only**)

**Interfaces:**
- Produces (async, принимают `conn: AsyncConnection`):
  - `async def peek_next_seq(conn, offer_code: str) -> int` — текущий next_seq (0 если нет).
  - `async def allocate_code_span(conn, offer_code: str, span: int) -> int` — атомарно бампает next_seq на span, возвращает `base` (первый номер диапазона).
  - `async def record_creative(conn, *, offer_code, code, kind, meta_creative_id, run_id) -> None` — INSERT в реестр `ON CONFLICT DO NOTHING`.

- [ ] **Step 1: Реализация** — `core/campaign_builder/creative_ledger.py`:

```python
"""Per-offer аллокатор сквозной нумерации кодов креативов + запись реестра.

Все функции работают в переданной транзакции (AsyncConnection) — вызывающий
управляет commit/rollback. Аллокация атомарна (UPDATE ... RETURNING под row-lock):
параллельные launch одного оффера получают непересекающиеся диапазоны.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def peek_next_seq(conn: AsyncConnection, offer_code: str) -> int:
    """Текущий high-water-mark номера кода (0 если по офферу ещё ничего не выдано)."""
    row = (
        await conn.execute(
            text("SELECT next_seq FROM offer_creative_seq WHERE offer_code = :c"),
            {"c": offer_code},
        )
    ).first()
    return int(row.next_seq) if row else 0


async def allocate_code_span(conn: AsyncConnection, offer_code: str, span: int) -> int:
    """Атомарно резервирует диапазон из span кодов, возвращает base (первый номер).

    span<=0 → возвращает (текущий next_seq + 1) без изменения счётчика.
    """
    if span <= 0:
        return await peek_next_seq(conn, offer_code) + 1
    new_seq = (
        await conn.execute(
            text(
                """
                INSERT INTO offer_creative_seq (offer_code, next_seq)
                VALUES (:c, :span)
                ON CONFLICT (offer_code) DO UPDATE
                    SET next_seq = offer_creative_seq.next_seq + EXCLUDED.next_seq
                RETURNING next_seq
                """
            ),
            {"c": offer_code, "span": span},
        )
    ).scalar_one()
    return int(new_seq) - span + 1


async def record_creative(
    conn: AsyncConnection,
    *,
    offer_code: str,
    code: str,
    kind: str,
    meta_creative_id: str,
    run_id: str | uuid.UUID | None,
) -> None:
    """Append-only запись созданного креатива (идемпотентно по (offer_code, code))."""
    await conn.execute(
        text(
            """
            INSERT INTO campaign_creative (offer_code, code, kind, meta_creative_id, run_id)
            VALUES (:offer, :code, :kind, :cid, :run_id)
            ON CONFLICT (offer_code, code) DO NOTHING
            """
        ),
        {"offer": offer_code, "code": code, "kind": kind,
         "cid": meta_creative_id, "run_id": str(run_id) if run_id else None},
    )
```

- [ ] **Step 2: Integration-тест (CI-only)** — `tests/integration/test_creative_ledger.py`:

```python
# Аллокатор: два последовательных резерва дают непересекающиеся диапазоны; peek не двигает.
import pytest
from core.campaign_builder.creative_ledger import (
    allocate_code_span, peek_next_seq, record_creative,
)

pytestmark = pytest.mark.integration

async def test_allocate_sequential(engine):
    async with engine.begin() as conn:
        base1 = await allocate_code_span(conn, "TST_LEDGER", 4)
        base2 = await allocate_code_span(conn, "TST_LEDGER", 3)
    assert base1 == 1 and base2 == 5  # 1..4, затем 5..7

async def test_peek_does_not_advance(engine):
    async with engine.begin() as conn:
        await allocate_code_span(conn, "TST_PEEK", 2)
        p1 = await peek_next_seq(conn, "TST_PEEK")
        p2 = await peek_next_seq(conn, "TST_PEEK")
    assert p1 == p2 == 2

async def test_record_idempotent(engine):
    async with engine.begin() as conn:
        await record_creative(conn, offer_code="TST_R", code="TST_R_CR001",
                              kind="image", meta_creative_id="111", run_id=None)
        await record_creative(conn, offer_code="TST_R", code="TST_R_CR001",
                              kind="image", meta_creative_id="111", run_id=None)
        cnt = (await conn.execute(__import__("sqlalchemy").text(
            "SELECT count(*) FROM campaign_creative WHERE offer_code='TST_R'"))).scalar()
    assert cnt == 1
```

> Чистка тестовых строк (`TST_*`) — в фикстуре teardown (prefix-scoped DELETE), по образцу money-тестов проекта.

- [ ] **Step 3: Проверка локально (статика)**

Run: `ruff check core/campaign_builder/creative_ledger.py`
Expected: clean. (БД-тесты — CI.)

- [ ] **Step 4: Коммит**

```bash
git add core/campaign_builder/creative_ledger.py tests/integration/test_creative_ledger.py
git commit -m "feat(campaign-builder): per-offer аллокатор кодов + реестр креативов"
```

---

### Task 8: API launch — аллокация; validate — peek

**Files:**
- Modify: `apps/api/routers/v1/campaigns_create.py` (validate ~387-430; launch ~432-519)
- Test: `tests/integration/test_api_campaigns_create.py` (дополнить; **CI-only**)

**Interfaces:**
- Consumes: `total_code_span` (Task 5), `peek_next_seq`/`allocate_code_span` (Task 7).
- Produces: при новом run'е `campaign_run.config->>'code_start'` = выделенная база; validate-превью использует peek.

- [ ] **Step 1: Реализация — validate peek**

В `validate_config` добавить `engine: DepEngine` в сигнатуру и проставить `config.code_start` из peek ДО `build_campaign_spec`:

```python
@router.post("/tools/campaigns/validate", response_model=ValidatePlanOut)
async def validate_config(body: ValidateIn, engine: DepEngine) -> ValidatePlanOut:
    try:
        config = body.domain_config()
        # Превью показывает реалистичные коды: продолжаем нумерацию оффера.
        async with engine.connect() as conn:
            config.code_start = await peek_next_seq(conn, config.offer_code) + 1
        spec = build_campaign_spec(config, concept_counts=body.concept_counts_map())
    ...
```

Импорты вверху файла:

```python
from core.campaign_builder.builder import build_campaign_spec, total_code_span
from core.campaign_builder.creative_ledger import allocate_code_span, peek_next_seq
```

- [ ] **Step 2: Реализация — launch аллокация (только для нового run'а)**

В `launch_campaign`, внутри `async with engine.begin() as conn:` после успешной вставки (когда `run_row is not None`, т.е. до `run_id = run_row.id` оставляем, а сразу после) — выделить диапазон и записать в config:

```python
        run_id = run_row.id

        # Per-offer нумерация: резервируем диапазон кодов ТОЛЬКО для реально нового
        # run'а (на конфликт-ветке аллокации нет → без gap'ов на повторах). code_start
        # фиксируется в config → воркер и retry берут одни и те же коды (preview==launch).
        span = total_code_span(config)
        base = await allocate_code_span(conn, config.offer_code, span)
        await conn.execute(
            text(
                "UPDATE campaign_run SET config = jsonb_set(config, '{code_start}', "
                "to_jsonb(:base)) WHERE id = CAST(:rid AS UUID)"
            ),
            {"base": base, "rid": run_id},
        )
```

> `ikey`/`config_json` считаются ДО аллокации с `code_start=1` (дефолт) — идемпотентный ключ стабилен между повторами; аллокация не влияет на ikey.

- [ ] **Step 3: Integration-тест (CI-only)** — добавить в `tests/integration/test_api_campaigns_create.py`:

```python
# Два launch'а одного оффера → code_start второго продолжает первый (нет коллизии CRxxx).
async def test_launch_allocates_continuing_code_start(client, engine):
    cfg = _valid_config()  # существующий хелпер; один блок, известное число ads
    r1 = await client.post("/api/tools/campaigns/launch", json={"config": cfg, ...})
    r2 = await client.post("/api/tools/campaigns/launch",
                           json={"config": {**cfg, "start_date": "2099-01-02"}, ...})
    # из campaign_run.config достаём code_start обоих
    ...
    assert base2 == base1 + span1
```

> Точную форму запроса взять из соседних тестов файла (там уже есть `_valid_config`/launch-вызовы). **Локально не гонять.**

- [ ] **Step 4: Проверка локально (статика + unit)**

Run: `ruff check apps/api/routers/v1/campaigns_create.py && pytest tests/unit -k campaigns_create -q`
Expected: ruff clean; unit (если есть) PASS. Integration — CI.

- [ ] **Step 5: Коммит**

```bash
git add apps/api/routers/v1/campaigns_create.py tests/integration/test_api_campaigns_create.py
git commit -m "feat(api): per-offer аллокация code_start на launch + peek в validate"
```

---

### Task 9: execute.py callback `on_creative_created` + worker пишет реестр

**Files:**
- Modify: `core/campaign_builder/execute.py` (типы; `_execute_block` ~290-340; `execute_campaign_spec` ~360-411)
- Modify: `apps/campaign_creator_worker/__init__.py` (вызов execute_campaign_spec — передать callback)
- Test: `tests/unit/test_execute_creative_callback.py` (создать; callback вызывается с правильными аргументами — БЕЗ БД, через фейковый client/uploader)

**Interfaces:**
- Produces: `CreativeCb = Callable[[str, str, str], Awaitable[None]]` (code, kind, meta_creative_id); `execute_campaign_spec(..., on_creative_created: CreativeCb | None = None)`.

- [ ] **Step 1: Тест — callback вызывается на каждый созданный creative**

```python
# execute_campaign_spec зовёт on_creative_created(code, kind, creative_id) на каждый ad.
# Используем фейковые client/uploader (как в существующих execute-unit-тестах).
import pytest
from core.campaign_builder.execute import execute_campaign_spec
# ... импорт фейков из tests/unit/test_execute_*.py (переиспользовать)

async def test_on_creative_created_called(monkeypatch):
    seen = []
    async def cb(code, kind, cid):
        seen.append((code, kind, cid))
    # собрать минимальный spec + fake client/uploader, как в соседних тестах execute
    ...
    await execute_campaign_spec(cfg, spec, concepts_by_campaign=cbc,
                                client=fake_client, uploader=fake_uploader,
                                on_creative_created=cb)
    assert {k for _, k, _ in seen} >= {"image"}  # типы по концептам
    assert all(cid for _, _, cid in seen)
```

> Переиспользовать существующие фейки `client`/`uploader` из `tests/unit/test_execute*.py` (grep). Если их нет — собрать минимальные заглушки, возвращающие предсказуемые id из `execute_graph_call`.

- [ ] **Step 2: Запустить — упадёт (нет параметра)**

Run: `pytest tests/unit/test_execute_creative_callback.py -q`
Expected: FAIL (`unexpected keyword 'on_creative_created'`).

- [ ] **Step 3: Реализация — execute.py**

Добавить тип рядом с `ProgressCb` (~47):

```python
CreativeCb = Callable[[str, str, str], Awaitable[None]]  # (code, kind, meta_creative_id)
```

`execute_campaign_spec` — добавить параметр и пробросить в `_execute_block`:

```python
async def execute_campaign_spec(
    cfg: CampaignConfig,
    spec: CampaignSpec,
    *,
    concepts_by_campaign: dict[str, list[ConceptInput]],
    client: _GraphClient,
    uploader: _Uploader,
    on_progress: ProgressCb | None = None,
    on_creative_created: CreativeCb | None = None,
) -> ExecutionResult:
    ...
            await _execute_block(
                cfg, spec_block, concepts,
                client=client, uploader=uploader, created=created, state=state,
                on_progress=on_progress, code_start=code_start,
                on_creative_created=on_creative_created,
            )
```

`_execute_block` — принять параметр и вызвать после извлечения creative_id (~334):

```python
async def _execute_block(
    cfg, spec_block, concepts, *,
    client, uploader, created, state, on_progress,
    code_start=1, on_creative_created=None,
):
    ...
            creative_id = _extract_id(resp, what=f"creative[{ad.code}]")
            created["creatives"].append(creative_id)
            state.creatives_done += 1
            await _emit(on_progress, state)
            if on_creative_created is not None:
                await on_creative_created(ad.code, ad.media_kind, creative_id)
```

- [ ] **Step 4: Реализация — worker пишет реестр**

В `apps/campaign_creator_worker/__init__.py`, где вызывается `execute_campaign_spec`, передать callback, замыкающий `engine` + `run_id` + `offer_code`:

```python
        async def _record(code: str, kind: str, creative_id: str) -> None:
            async with engine.begin() as conn:
                await record_creative(
                    conn, offer_code=cfg.offer_code, code=code, kind=kind,
                    meta_creative_id=creative_id, run_id=run_id,
                )

        result = await execute_campaign_spec(
            cfg, spec,
            concepts_by_campaign=concepts_by_campaign,
            client=client, uploader=uploader,
            on_progress=on_progress, on_creative_created=_record,
        )
```

Импорт: `from core.campaign_builder.creative_ledger import record_creative`.

> Точные имена локальных переменных (`cfg`, `run_id`, `engine`, `concepts_by_campaign`, `on_progress`) сверить с фактическим телом воркера у вызова execute_campaign_spec.

- [ ] **Step 5: Запустить — зелёно**

Run: `pytest tests/unit/test_execute_creative_callback.py -q && ruff check core/campaign_builder/execute.py apps/campaign_creator_worker/`
Expected: PASS + clean.

- [ ] **Step 6: Коммит**

```bash
git add core/campaign_builder/execute.py apps/campaign_creator_worker/__init__.py tests/unit/test_execute_creative_callback.py
git commit -m "feat(execute): on_creative_created callback → запись реестра в воркере"
```

---

## ФАЗА 3 — Frontend web (`frontend/`)

### Task 10: типы + buildConfig без kind-фильтра

**Files:**
- Modify: `frontend/src/lib/api/campaigns.ts` (`CampaignStructure` ~82-92; `ValidatePlanOut` campaign `kind` ~172)
- Modify: `frontend/src/stores/campaignWizard.ts` (`buildConfig` ~255-272)
- Test: `frontend/src/tests/domain/campaignWizard.buildConfig.test.ts` (обновить)

**Interfaces:**
- Produces: `CampaignStructure` без `kind`; `buildConfig` отдаёт кампании со ВСЕМИ привязанными концептами (без фильтра по типу).

- [ ] **Step 1: Тест — кампания получает и фото, и видео концепты**

Обновить `campaignWizard.buildConfig.test.ts`: тест, где блок получает И `img.jpg`, И `clip.mp4` (раньше kind-фильтр их разводил):

```ts
it("кампания получает смешанные концепты (фото+видео) без kind-фильтра", () => {
  const concepts: UploadedConcept[] = [
    { ref: "img.jpg", original_name: "img.jpg", size_bytes: 1, content_type: "image/jpeg", campaign_keys: ["c1"] },
    { ref: "clip.mp4", original_name: "clip.mp4", size_bytes: 1, content_type: "video/mp4", campaign_keys: ["c1"] },
  ];
  seedStore(concepts, [{ key: "c1", adset_count: 2 }]);
  const config = useWizardStore.getState().buildConfig();
  expect(config.campaigns[0]!.concept_refs).toEqual(["img.jpg", "clip.mp4"]);
});
```

> `seedStore`/`CampaignStructure` в тестах больше не принимают `kind` — обновить хелперы теста.

- [ ] **Step 2: Запустить — упадёт (kind в типе/фильтре)**

Run: `pnpm --filter fb-stop-bot-frontend exec vitest run src/tests/domain/campaignWizard.buildConfig.test.ts`
Expected: FAIL / tsc-ошибка по `kind`.

- [ ] **Step 3: Реализация — типы**

`lib/api/campaigns.ts` — убрать `kind` из `CampaignStructure`:

```ts
export interface CampaignStructure {
  /** Ключ кампании (уникальный в рамках конфига, напр. "camp1") */
  key: string;
  /** Число adset'ов в этой кампании */
  adset_count: number;
  /** Привязанные ref концептов (из upload_id) — смешанные фото/видео */
  concept_refs: string[];
}
```

В `ValidatePlanOut` (campaign-блок) сделать `kind` опциональным или убрать (если поле было `kind: string`):

```ts
  // kind убран — спека больше не типизирует кампанию (медиа per-concept)
```

- [ ] **Step 4: Реализация — buildConfig**

`stores/campaignWizard.ts` — убрать фильтр по типу (оставить только привязку по `campaign_keys`):

```ts
    const campaignsWithRefs: CampaignConfig["campaigns"] = structure.campaigns.map((block) => {
      const refs = creatives.concepts
        .filter((c) => c.campaign_keys.length === 0 || c.campaign_keys.includes(block.key))
        .map((c) => c.ref);
      return { ...block, concept_refs: refs };
    });
```

Удалить более ненужные `VIDEO_EXTS`/`refKind` в этом файле, если они использовались только для фильтра.

- [ ] **Step 5: Запустить — зелёно + tsc**

Run: `pnpm --filter fb-stop-bot-frontend exec vitest run src/tests/domain/ && pnpm --filter fb-stop-bot-frontend exec tsc --noEmit`
Expected: PASS + tsc clean.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/lib/api/campaigns.ts frontend/src/stores/campaignWizard.ts frontend/src/tests/domain/campaignWizard.buildConfig.test.ts
git commit -m "feat(web-wizard): CampaignStructure без kind, buildConfig без kind-фильтра"
```

---

### Task 11: WizardStep4Structure — одна кнопка «+ Кампания»

**Files:**
- Modify: `frontend/src/components/domain/campaigns/WizardStep4Structure.tsx`
- Test: `frontend/src/tests/...` (если есть тест Step4 — обновить; иначе покрытие через CampaignCreate.test.tsx)

- [ ] **Step 1: Реализация** — убрать `kind` из `genKey`/`addCampaign`/`CampaignRow`, заменить две кнопки на одну:

```tsx
function genKey(campaigns: CampaignStructure[]): string {
  return `camp${campaigns.length + 1}`;
}

const addCampaign = () => {
  onChange([...campaigns, { key: genKey(campaigns), adset_count: 3, concept_refs: [] }]);
};
```

Кнопки (и в empty-state, и в «добавить ещё») — одна:

```tsx
<Button variant="secondary" size="sm" leftIcon={<Plus size={13} />} onClick={addCampaign}>
  + Кампания
</Button>
```

`CampaignRow`: убрать `isVideo`/иконку типа/бейдж STATIC|VIDEO. Иконку оставить нейтральной (`Layers`), показывать `#index` + `key` + поле «Число adset'ов N» + удалить. Обновить подсказку в шапке:

```tsx
<p className="text-[13px] text-bg-9 mt-1">
  Добавьте кампании и число adset'ов N. Концепты (фото и видео) привяжете на след. шаге —
  один adset может держать и фото-, и видео-объявления.
</p>
```

Импорты: убрать `Image`/`Video`, добавить `Plus`/`Layers` из lucide.

- [ ] **Step 2: Проверка — tsc + lint + vitest**

Run: `pnpm --filter fb-stop-bot-frontend exec tsc --noEmit && npx --prefix frontend eslint frontend/src/components/domain/campaigns/WizardStep4Structure.tsx --max-warnings 0 && pnpm --filter fb-stop-bot-frontend exec vitest run src/tests/pages/CampaignCreate.test.tsx`
Expected: PASS. Обновить тесты, которые искали «+ Фото»/«+ Видео»/STATIC|VIDEO.

- [ ] **Step 3: Коммит**

```bash
git add frontend/src/components/domain/campaigns/WizardStep4Structure.tsx frontend/src/tests/
git commit -m "feat(web-wizard): шаг 4 — одна кнопка «+ Кампания» без типа"
```

---

### Task 12: WizardStep5Creatives — привязка без kind, mixed-счётчики

**Files:**
- Modify: `frontend/src/components/domain/campaigns/WizardStep5Creatives.tsx`
- Test: покрытие через CampaignCreate.test.tsx

- [ ] **Step 1: Реализация** — в карточке концепта показывать иконку типа по `content_type` (image/video), привязка к кампаниям остаётся через `campaign_keys` (toggle уже есть). В строке кампании (если отображается сводка) показывать «X фото + Y видео» из привязанных концептов:

```tsx
const isVideo = (c: UploadedConcept) =>
  (c.content_type ?? "").startsWith("video") || /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(c.ref);
```

Любые места, читавшие `campaign.kind` для фильтра привязки, убрать — концепт можно привязать к любой кампании. Если был title `${c.kind} / ${c.adset_count} adsets` (строка ~304) — заменить на счётчики типа:

```tsx
title={`${campaign.adset_count} adsets`}
```

- [ ] **Step 2: Проверка — tsc + lint + vitest**

Run: `pnpm --filter fb-stop-bot-frontend exec tsc --noEmit && npx --prefix frontend eslint frontend/src/components/domain/campaigns/WizardStep5Creatives.tsx --max-warnings 0 && pnpm --filter fb-stop-bot-frontend exec vitest run src/tests/pages/CampaignCreate.test.tsx`
Expected: PASS.

- [ ] **Step 3: Коммит**

```bash
git add frontend/src/components/domain/campaigns/WizardStep5Creatives.tsx frontend/src/tests/
git commit -m "feat(web-wizard): шаг 5 — привязка смешанных концептов без kind-фильтра"
```

---

### Task 13: WizardStep6Preview — без kind-токена, empty-block по concept_refs

**Files:**
- Modify: `frontend/src/components/domain/campaigns/WizardStep6Preview.tsx` (~79-90, ~273-276)
- Test: покрытие через CampaignCreate.test.tsx

- [ ] **Step 1: Реализация**

Заменить комментарий и логику `emptyBlocks` (без упоминания kind-фильтра — теперь блок пуст, если ему не привязали концептов):

```tsx
  // Блоки без концептов: байер не привязал ни одного концепта на шаге 5. Launch
  // отобьёт 422 — предупреждаем заранее, чтобы вернулись на шаг 5.
  const emptyBlocks = config.campaigns
    .filter((c) => (c.concept_refs ?? []).length === 0)
    .map((c) => c.key);
```

Убрать `{campaign.kind} · ` из строки кампании (~273):

```tsx
        <span className="text-[10px] text-bg-7 font-display uppercase tracking-wider shrink-0">
          {campaign.adsets.length} adset{campaign.adsets.length !== 1 ? "s" : ""}
        </span>
```

- [ ] **Step 2: Проверка — tsc + lint + vitest (полный web)**

Run: `pnpm --filter fb-stop-bot-frontend exec tsc --noEmit && pnpm --filter fb-stop-bot-frontend lint && pnpm --filter fb-stop-bot-frontend exec vitest run`
Expected: tsc clean, lint 0, все vitest PASS.

- [ ] **Step 3: Коммит**

```bash
git add frontend/src/components/domain/campaigns/WizardStep6Preview.tsx frontend/src/tests/
git commit -m "feat(web-wizard): шаг 6 — превью без kind, empty-block по привязке концептов"
```

---

## ФАЗА 4 — Frontend mini (`frontend-mini/`)

### Task 14: mini — структура без типа, концепты на все кампании

**Files:**
- Modify: `frontend-mini/src/routes/campaigns/-wizardStore.ts` (тип `CampaignStructure`)
- Modify: `frontend-mini/src/routes/campaigns/StepStructure.tsx` (убрать Select типа, дефолт add)
- Modify: `frontend-mini/src/routes/campaigns/StepCreatives.tsx` (~102-112: concept_refs = все концепты)
- Test: `frontend-mini/src/tests/campaigns.steps.test.tsx` (обновить)

- [ ] **Step 1: Реализация — тип**

В `-wizardStore.ts` (тип структуры кампании) убрать `kind` (поле и `KIND_OPTIONS` если объявлены там).

- [ ] **Step 2: Реализация — StepStructure**

Убрать `KIND_OPTIONS`, Select типа и `Badge` с `spec.kind`; `addCampaign` без `kind`:

```tsx
function addCampaign() {
  setCampaigns((prev) => [...prev, { key: `camp_${prev.length + 1}`, adset_count: 3 }]);
}
```

Дефолтная инициализация (`~95`): `[{ key: "camp_1", adset_count: 3 }]`.

- [ ] **Step 3: Реализация — StepCreatives**

Каждая кампания получает ВСЕ концепты (смешанные), без kind-фильтра (~102-112):

```tsx
    // Один adset может держать фото и видео — кампания получает все концепты.
    const campaignsWithRefs = campaigns.map((c) => ({
      ...c,
      concept_refs: allRefs,
    }));
```

Удалить локальные `VIDEO_EXTS`/`refKind`, если использовались только тут.

- [ ] **Step 4: Проверка — tsc + lint + vitest (mini)**

Run: `pnpm --filter fb-agent-mini exec tsc --noEmit && pnpm --filter fb-agent-mini lint && pnpm --filter fb-agent-mini exec vitest run`
Expected: tsc clean, lint 0, vitest PASS. Обновить `campaigns.steps.test.tsx` (искал тип/Select).

- [ ] **Step 5: Коммит**

```bash
git add frontend-mini/src/routes/campaigns/ frontend-mini/src/tests/
git commit -m "feat(mini-wizard): структура без типа, концепты на все кампании (смешанные медиа)"
```

---

## ФАЗА 5 — Прогон и деплой

### Task 15: Полный локальный прогон + push (после OK пользователя)

- [ ] **Step 1: Backend статика + unit**

Run: `ruff check . && pytest tests/unit -q`
Expected: ruff clean; unit PASS.

- [ ] **Step 2: Frontend оба пакета**

Run: `pnpm --filter fb-stop-bot-frontend exec tsc --noEmit && pnpm --filter fb-stop-bot-frontend lint && pnpm --filter fb-stop-bot-frontend exec vitest run && pnpm --filter fb-agent-mini exec tsc --noEmit && pnpm --filter fb-agent-mini lint && pnpm --filter fb-agent-mini exec vitest run`
Expected: всё зелёное.

- [ ] **Step 3: Спросить пользователя про push.** Integration-тесты (миграция, аллокатор, реестр, API allocation) гоняются на изолированной БД в **CI** после push — это финальная проверка money-путей. Сообщить и дождаться OK.

- [ ] **Step 4: Push + watch CI**

```bash
git push origin main
gh run watch $(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId') --exit-status
```
Expected: CI зелёный (ruff + pytest полный incl. integration + build + deploy).

---

## Self-Review (выполнено автором плана)

- **Покрытие спеки:** §1 смешанные медиа → Task 1-4,10-14; §2 нумерация → Task 5-8; §3 реестр → Task 6,7,9; §4 frontend → Task 10-14; §5 миграция/backfill → Task 6; тесты → в каждой задаче + Task 15.
- **Плейсхолдеры:** места «сверить с фактическим кодом» помечены явно (фабрики тестов, имена локальных переменных воркера) — это указания исполнителю свериться, не пропуски кода; основной код приведён.
- **Согласованность типов:** `code_start` (поле config) единообразно в Task 5/8; `total_code_span`/`peek_next_seq`/`allocate_code_span`/`record_creative`/`on_creative_created` имена совпадают между Task 5,7,8,9; `CampaignStructure` без `kind` согласован Task 10-14.
