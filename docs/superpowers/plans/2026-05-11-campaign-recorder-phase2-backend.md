# Campaign Creator — Фаза 2A: Бэкенд

> **Ждёт:** Фаза 1A + реальный анализ записанных сессий.  
> Steps пишутся ПОСЛЕ того как посмотрели реальные данные из recordings/*.json.

**Цель:** Playwright-воркер создаёт кампанию пошагово с checkpoint-паузами и подтверждениями из UI.

**Архитектура:** Новая таблица `CampaignCreatorTask` в БД (аналог `DisableTask`). Воркер-runner читает Steps из `core/campaign_creator/steps/`, на каждом опасном шаге ставит статус `waiting_confirmation` и ждёт вызова `POST /confirm`. Каждый Step — отдельный класс.

**Стек:** Python 3.12, Playwright async, SQLAlchemy 2.x async, Alembic, FastAPI.

---

## Файловая карта

| Действие | Файл |
|----------|------|
| Создать | `core/campaign_creator/__init__.py` |
| Создать | `core/campaign_creator/steps/__init__.py` |
| Создать | `core/campaign_creator/steps/base.py` |
| Создать | `core/campaign_creator/steps/create_campaign.py` |
| Создать | `core/campaign_creator/steps/set_geo.py` |
| Создать | `core/campaign_creator/steps/upload_media.py` |
| Создать | `core/campaign_creator/steps/set_url_params.py` |
| Создать | `core/campaign_creator/runner.py` |
| Изменить | `core/domain.py` (новый enum) |
| Изменить | `core/models/__init__.py` (новая модель) |
| Создать | `migrations/versions/XXXX_add_campaign_creator_task.py` |
| Создать | `apps/api/routers/campaign_creator.py` |
| Изменить | `apps/api/schemas.py` |
| Изменить | `apps/api/main.py` |
| Создать | `tests/unit/test_campaign_creator.py` |

---

### Task 1: Новый enum и модель БД

**Файлы:**
- Изменить: `core/domain.py`
- Изменить: `core/models/__init__.py`

- [ ] **Шаг 1.1: Добавить `CampaignCreatorTaskStatus` в `core/domain.py`**

В конец файла добавить:

```python
class CampaignCreatorTaskStatus(StrEnum):
    """Статус задачи автоматического создания кампании."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
```

- [ ] **Шаг 1.2: Добавить модель `CampaignCreatorTask` в `core/models/__init__.py`**

В конец файла (после последней модели) добавить:

```python
_CAMPAIGN_CREATOR_STATUS_ENUM = Enum(
    CampaignCreatorTaskStatus,
    name="campaign_creator_task_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class CampaignCreatorTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Задача автоматического создания кампании в Ads Manager."""

    __tablename__ = "campaign_creator_tasks"

    offer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    creative_folder: Mapped[str] = mapped_column(String(256), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cdp_url: Mapped[str] = mapped_column(String(256), nullable=False)

    status: Mapped[CampaignCreatorTaskStatus] = mapped_column(
        _CAMPAIGN_CREATOR_STATUS_ENUM,
        default=CampaignCreatorTaskStatus.PENDING,
        nullable=False,
    )

    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
```

Также добавить импорт в блок импортов `core/domain.py`:
```python
from core.domain import (
    ...,
    CampaignCreatorTaskStatus,
)
```

- [ ] **Шаг 1.3: Создать миграцию Alembic**

```bash
alembic revision --autogenerate -m "add_campaign_creator_task"
```

Проверить сгенерированный файл в `migrations/versions/` — убедиться что создаётся таблица `campaign_creator_tasks` с нужными колонками.

- [ ] **Шаг 1.4: Применить миграцию**

```bash
alembic upgrade head
```
Ожидаем: `Running upgrade ... -> ..., add_campaign_creator_task`

- [ ] **Шаг 1.5: Коммит**

```bash
git add core/domain.py core/models/__init__.py migrations/
git commit -m "feat: campaign_creator — модель CampaignCreatorTask + миграция"
```

---

### Task 2: Base Step и первые шаги

**Файлы:**
- Создать: `core/campaign_creator/steps/base.py`
- Создать: `core/campaign_creator/steps/create_campaign.py`
- Создать: `core/campaign_creator/steps/set_geo.py`
- Тест: `tests/unit/test_campaign_creator.py`

- [ ] **Шаг 2.1: Написать падающий тест для base.py**

```python
# tests/unit/test_campaign_creator.py
# Проверяем базовый интерфейс Step
import pytest
from unittest.mock import AsyncMock, MagicMock

def test_base_step_has_required_methods():
    """Каждый Step должен иметь name, is_checkpoint, execute()."""
    from core.campaign_creator.steps.base import BaseStep
    # BaseStep — абстрактный класс, нельзя инстанцировать напрямую
    assert hasattr(BaseStep, 'name')
    assert hasattr(BaseStep, 'is_checkpoint')
    assert hasattr(BaseStep, 'execute')
```

- [ ] **Шаг 2.2: Запустить тест, убедиться что падает**

```bash
pytest tests/unit/test_campaign_creator.py::test_base_step_has_required_methods -v
```

- [ ] **Шаг 2.3: Создать `core/campaign_creator/__init__.py`**

```python
# -*- coding: utf-8 -*-
"""Модуль автоматического создания кампаний в Ads Manager."""
```

- [ ] **Шаг 2.4: Создать `core/campaign_creator/steps/__init__.py`**

```python
# -*- coding: utf-8 -*-
"""Шаги создания кампании."""
```

- [ ] **Шаг 2.5: Создать `core/campaign_creator/steps/base.py`**

```python
# -*- coding: utf-8 -*-
"""Базовый класс шага создания кампании."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from playwright.async_api import Page


@dataclass
class StepContext:
    """Контекст выполнения шага — общие данные для всего прогона."""
    offer_code: str
    creative_folder: str
    cabinet_id: str
    campaign_name: str
    cdp_url: str
    extra: dict


@dataclass
class StepResult:
    """Результат выполнения шага."""
    success: bool
    message: str
    checkpoint_data: dict | None = None


class BaseStep(ABC):
    """Базовый класс шага создания кампании."""

    name: str = "base"
    is_checkpoint: bool = False  # Если True — runner ждёт подтверждения после шага

    @abstractmethod
    async def execute(self, page: Page, context: StepContext) -> StepResult:
        """Выполнить шаг в браузере."""
        ...
```

- [ ] **Шаг 2.6: Создать `core/campaign_creator/steps/create_campaign.py`**

> ⚠️ Конкретные селекторы (CSS/XPath) ЗАПОЛНИТЬ после анализа реальных записей из Фазы 1.
> Сейчас ставим PLACEHOLDER_SELECTOR с TODO-комментарием.

```python
# -*- coding: utf-8 -*-
"""Шаг: создать новую кампанию в Ads Manager."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

# TODO: заполнить после анализа записи сессии
_BTN_CREATE_CAMPAIGN = "[aria-label='Создать кампанию']"
_INPUT_CAMPAIGN_NAME = "[aria-label='Название кампании']"
_BTN_CONVERSION = "[aria-label='Конверсии']"


class CreateCampaignStep(BaseStep):
    """Нажать 'Создать' → выбрать 'Конверсии' → ввести название кампании."""

    name = "create_campaign"
    is_checkpoint = True  # Перед публикацией — обязательное подтверждение

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await page.click(_BTN_CREATE_CAMPAIGN, timeout=10_000)
            logger.info("Нажали 'Создать кампанию'")

            await page.click(_BTN_CONVERSION, timeout=10_000)
            logger.info("Выбрали цель 'Конверсии'")

            await page.fill(_INPUT_CAMPAIGN_NAME, context.campaign_name)
            logger.info("Ввели название кампании: %s", context.campaign_name)

            return StepResult(
                success=True,
                message=f"Кампания создана: {context.campaign_name}",
                checkpoint_data={"campaign_name": context.campaign_name},
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка шага create_campaign: {exc}")
```

- [ ] **Шаг 2.7: Создать `core/campaign_creator/steps/set_geo.py`**

```python
# -*- coding: utf-8 -*-
"""Шаг: настроить гео в группе объявлений."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

# TODO: заполнить после анализа записи
_INPUT_GEO_SEARCH = "[placeholder='Поиск местоположения']"
_BTN_REMOVE_INITIAL_GEO = "[aria-label='Удалить']"


class SetGeoStep(BaseStep):
    """Добавить Антарктику + страну оффера, удалить первоначальное гео."""

    name = "set_geo"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        from core.campaign_scripts.planner import ANTARCTICA_LOCATION
        locations = [ANTARCTICA_LOCATION, context.extra.get("offer_country_name", "")]
        try:
            for location in locations:
                if not location:
                    continue
                await page.fill(_INPUT_GEO_SEARCH, location, timeout=10_000)
                await page.keyboard.press("Enter")
                logger.info("Добавили гео: %s", location)
            return StepResult(success=True, message=f"Гео настроено: {', '.join(locations)}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка шага set_geo: {exc}")
```

- [ ] **Шаг 2.8: Запустить тест, убедиться что проходит**

```bash
pytest tests/unit/test_campaign_creator.py::test_base_step_has_required_methods -v
```
Ожидаем: `PASSED`

- [ ] **Шаг 2.9: Коммит**

```bash
git add core/campaign_creator/
git commit -m "feat: campaign_creator — BaseStep, CreateCampaignStep, SetGeoStep"
```

---

### Task 3: runner.py — исполнитель шагов с checkpoint-паузами

**Файлы:**
- Создать: `core/campaign_creator/runner.py`
- Тест: `tests/unit/test_campaign_creator.py`

- [ ] **Шаг 3.1: Написать падающий тест**

```python
# Проверяем что runner останавливается на checkpoint шаге и ждёт подтверждения
@pytest.mark.asyncio
async def test_runner_pauses_on_checkpoint():
    """Runner должен установить статус WAITING_CONFIRMATION на checkpoint-шаге."""
    from core.campaign_creator.runner import CampaignCreatorRunner
    from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult

    class FakeCheckpointStep(BaseStep):
        name = "fake_checkpoint"
        is_checkpoint = True
        async def execute(self, page, context):
            return StepResult(success=True, message="ok")

    class FakeNormalStep(BaseStep):
        name = "fake_normal"
        is_checkpoint = False
        async def execute(self, page, context):
            return StepResult(success=True, message="ok")

    statuses = []
    async def mock_set_status(status, step=None, data=None):
        statuses.append(status)

    mock_page = AsyncMock()
    context = StepContext(
        offer_code="DRC_CR2", creative_folder="test", cabinet_id="123",
        campaign_name="MV | DRC", cdp_url="ws://localhost:9222", extra={}
    )

    runner = CampaignCreatorRunner(
        steps=[FakeNormalStep(), FakeCheckpointStep()],
        set_status=mock_set_status,
    )
    # После checkpoint runner должен остановиться
    await runner.run_until_checkpoint(mock_page, context)
    assert "WAITING_CONFIRMATION" in statuses
```

- [ ] **Шаг 3.2: Запустить тест, убедиться что падает**

```bash
pytest tests/unit/test_campaign_creator.py::test_runner_pauses_on_checkpoint -v
```

- [ ] **Шаг 3.3: Создать `core/campaign_creator/runner.py`**

```python
# -*- coding: utf-8 -*-
"""Runner шагов создания кампании с checkpoint-паузами."""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from playwright.async_api import Page

from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult
from core.domain import CampaignCreatorTaskStatus

logger = logging.getLogger(__name__)


class CampaignCreatorRunner:
    """Выполняет список шагов, останавливается на checkpoint для подтверждения."""

    def __init__(
        self,
        steps: list[BaseStep],
        set_status: Callable[..., Awaitable[None]],
    ) -> None:
        self._steps = steps
        self._set_status = set_status
        self._current_index = 0

    async def run_until_checkpoint(self, page: Page, context: StepContext) -> bool:
        """Выполняет шаги до первого checkpoint или до конца.

        Возвращает True если все шаги пройдены, False если ждёт подтверждения.
        """
        while self._current_index < len(self._steps):
            step = self._steps[self._current_index]
            logger.info("Выполняю шаг %d/%d: %s", self._current_index + 1, len(self._steps), step.name)

            await self._set_status(
                CampaignCreatorTaskStatus.RUNNING,
                step=step.name,
            )

            result: StepResult = await step.execute(page, context)

            if not result.success:
                await self._set_status(
                    CampaignCreatorTaskStatus.FAILED,
                    step=step.name,
                    data={"error": result.message},
                )
                logger.error("Шаг %s провалился: %s", step.name, result.message)
                return False

            logger.info("Шаг %s завершён: %s", step.name, result.message)
            self._current_index += 1

            if step.is_checkpoint:
                await self._set_status(
                    CampaignCreatorTaskStatus.WAITING_CONFIRMATION,
                    step=step.name,
                    data=result.checkpoint_data,
                )
                logger.info("Checkpoint '%s' — ожидаю подтверждения", step.name)
                return False

        await self._set_status(CampaignCreatorTaskStatus.SUCCEEDED)
        return True

    def confirm_and_continue(self) -> None:
        """Сбрасывает паузу после подтверждения пользователем."""
        # Состояние уже обновлено через set_status — просто разрешаем следующий вызов run_until_checkpoint
        pass
```

- [ ] **Шаг 3.4: Запустить тест, убедиться что проходит**

```bash
pytest tests/unit/test_campaign_creator.py::test_runner_pauses_on_checkpoint -v
```
Ожидаем: `PASSED`

- [ ] **Шаг 3.5: Коммит**

```bash
git add core/campaign_creator/runner.py
git commit -m "feat: campaign_creator — runner с checkpoint-паузами"
```

---

### Task 4: API роутер campaign_creator

**Файлы:**
- Создать: `apps/api/routers/campaign_creator.py`
- Изменить: `apps/api/schemas.py`
- Изменить: `apps/api/main.py`

- [ ] **Шаг 4.1: Добавить схемы в `apps/api/schemas.py`**

```python
# --- Campaign Creator ---

class CampaignCreatorStartRequestSchema(BaseModel):
    """Запрос на запуск автосоздания кампании."""
    offer_code: str
    creative_folder: str
    cabinet_id: str
    cdp_url: str = "ws://localhost:9222"


class CampaignCreatorTaskSchema(BaseModel):
    """Статус задачи автосоздания."""
    id: str
    status: str
    current_step: str | None
    checkpoint_data: dict | None
    error_message: str | None
    campaign_name: str | None
    offer_code: str
    created_at: str
```

- [ ] **Шаг 4.2: Создать `apps/api/routers/campaign_creator.py`**

```python
# -*- coding: utf-8 -*-
"""FastAPI роутер для управления задачами автосоздания кампаний."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    CampaignCreatorStartRequestSchema,
    CampaignCreatorTaskSchema,
)
from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import StepContext
from core.campaign_creator.steps.create_campaign import CreateCampaignStep
from core.campaign_creator.steps.set_geo import SetGeoStep
from core.campaign_recorder.cdp_session import CdpSession
from core.domain import CampaignCreatorTaskStatus
from core.models import CampaignCreatorTask, Offer
from core.campaign_scripts.planner import build_campaign_script_plan, CampaignScriptConfig
from core.campaign_scripts.creative_folder import inspect_creative_folder

router = APIRouter(prefix="/api/campaign-creator", tags=["campaign-creator"])
logger = logging.getLogger(__name__)

# Активные runner'ы: task_id → runner
_active_runners: dict[str, CampaignCreatorRunner] = {}


def _task_to_schema(task: CampaignCreatorTask) -> CampaignCreatorTaskSchema:
    return CampaignCreatorTaskSchema(
        id=str(task.id),
        status=task.status.value,
        current_step=task.current_step,
        checkpoint_data=task.checkpoint_data,
        error_message=task.error_message,
        campaign_name=task.campaign_name,
        offer_code=task.offer_code,
        created_at=task.created_at.isoformat(),
    )


@router.post("/start", response_model=CampaignCreatorTaskSchema)
async def start_campaign_creator(
    body: CampaignCreatorStartRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    """Создать задачу автосоздания кампании и запустить воркер."""
    offer_row = await db.execute(select(Offer).where(Offer.code == body.offer_code.upper()))
    offer = offer_row.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Оффер не найден")

    folder = await inspect_creative_folder(body.creative_folder)
    plan = build_campaign_script_plan(
        folder=folder,
        config=CampaignScriptConfig(
            offer_code=body.offer_code,
            offer_country_name=offer.country_name or "",
            cabinet_id=body.cabinet_id,
        ),
    )

    task = CampaignCreatorTask(
        offer_code=body.offer_code.upper(),
        creative_folder=body.creative_folder,
        cabinet_id=body.cabinet_id,
        cdp_url=body.cdp_url,
        status=CampaignCreatorTaskStatus.PENDING,
        campaign_name=plan.campaign_name,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    task_id = str(task.id)

    async def _set_status(status, step=None, data=None):
        async with db.__class__(bind=db.get_bind()) as session:
            row = await session.get(CampaignCreatorTask, task.id)
            if row:
                row.status = status
                if step:
                    row.current_step = step
                if data:
                    row.checkpoint_data = data
                if status == CampaignCreatorTaskStatus.FAILED and data:
                    row.error_message = data.get("error")
                await session.commit()

    context = StepContext(
        offer_code=body.offer_code,
        creative_folder=body.creative_folder,
        cabinet_id=body.cabinet_id,
        campaign_name=plan.campaign_name,
        cdp_url=body.cdp_url,
        extra={"offer_country_name": offer.country_name or ""},
    )

    steps = [CreateCampaignStep(), SetGeoStep()]
    runner = CampaignCreatorRunner(steps=steps, set_status=_set_status)
    _active_runners[task_id] = runner

    async def _run():
        session = CdpSession(cdp_url=body.cdp_url)
        async with session.connect() as page:
            await runner.run_until_checkpoint(page, context)

    asyncio.create_task(_run())
    return _task_to_schema(task)


@router.post("/{task_id}/confirm", response_model=CampaignCreatorTaskSchema)
async def confirm_checkpoint(task_id: str, db: AsyncSession = Depends(get_db)):
    """Подтвердить checkpoint и продолжить выполнение."""
    from uuid import UUID
    task = await db.get(CampaignCreatorTask, UUID(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task.status != CampaignCreatorTaskStatus.WAITING_CONFIRMATION:
        raise HTTPException(status_code=400, detail="Задача не ожидает подтверждения")

    task.status = CampaignCreatorTaskStatus.CONFIRMED
    await db.commit()

    runner = _active_runners.get(task_id)
    if runner:
        async def _set_status(status, step=None, data=None):
            async with db.__class__(bind=db.get_bind()) as session:
                row = await session.get(CampaignCreatorTask, task.id)
                if row:
                    row.status = status
                    if step:
                        row.current_step = step
                    if data:
                        row.checkpoint_data = data
                    await session.commit()

        context = StepContext(
            offer_code=task.offer_code,
            creative_folder=task.creative_folder,
            cabinet_id=task.cabinet_id,
            campaign_name=task.campaign_name or "",
            cdp_url=task.cdp_url,
            extra={},
        )
        runner._set_status = _set_status

        async def _continue():
            from core.campaign_recorder.cdp_session import CdpSession
            session = CdpSession(cdp_url=task.cdp_url)
            async with session.connect() as page:
                await runner.run_until_checkpoint(page, context)

        asyncio.create_task(_continue())

    await db.refresh(task)
    return _task_to_schema(task)


@router.get("/{task_id}/status", response_model=CampaignCreatorTaskSchema)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """Получить статус задачи автосоздания."""
    from uuid import UUID
    task = await db.get(CampaignCreatorTask, UUID(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _task_to_schema(task)
```

- [ ] **Шаг 4.3: Подключить роутер в `apps/api/main.py`**

```python
from apps.api.routers.campaign_creator import router as campaign_creator_router
# ...
app.include_router(campaign_creator_router)
```

- [ ] **Шаг 4.4: Проверить импорты**

```bash
python -c "from apps.api.main import app; print('OK')"
```
Ожидаем: `OK`

- [ ] **Шаг 4.5: Коммит**

```bash
git add apps/api/routers/campaign_creator.py apps/api/schemas.py apps/api/main.py
git commit -m "feat: campaign_creator — API роутер start/confirm/status"
```

---

### Task 5: Финальный прогон

- [ ] **Шаг 5.1: Все тесты**

```bash
pytest tests/unit/test_campaign_creator.py tests/unit/test_campaign_recorder.py -v
```
Ожидаем: все `PASSED`

- [ ] **Шаг 5.2: Общий прогон unit-тестов**

```bash
pytest tests/unit/ -q --tb=short
```

- [ ] **Шаг 5.3: Линтер**

```bash
ruff check core/campaign_creator/ apps/api/routers/campaign_creator.py
```

- [ ] **Шаг 5.4: Итоговый коммит**

```bash
git commit -m "feat: campaign_creator фаза 2A — бэкенд готов"
```
