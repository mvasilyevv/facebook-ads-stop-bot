# -*- coding: utf-8 -*-
"""API роутер для автоматического создания кампаний в Ads Manager.

Поддерживает три режима выполнения:
  - /start                           — полный пайплайн (full autopilot)
  - /{task_id}/run-step/{step_name}  — один шаг на текущей странице
  - /{task_id}/run-from/{step_name}  — от указанного шага до конца
  - /{task_id}/resume                — продолжить с упавшего шага
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from apps.api.schemas import (
    CampaignCreatorStartRequestSchema,
    CampaignCreatorStepInfoSchema,
    CampaignCreatorStepsListSchema,
    CampaignCreatorTaskSchema,
)
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.campaign_creator.context_codec import context_from_dict, context_to_dict
from core.campaign_creator.naming import build_campaign_name
from core.campaign_creator.step_executor import execute_steps, open_page
from core.campaign_creator.steps.base import AdsetSpec, BaseStep, StepContext
from core.campaign_creator.steps.registry import (
    STEPS_ORDER,
    build_pipeline,
    build_step,
    step_idempotent,
)
from core.config import get_settings
from core.db import get_session_factory
from core.domain import CampaignCreatorTaskStatus
from core.models import CampaignCreatorTask, Offer

router = APIRouter(prefix="/api/campaign-creator", tags=["campaign-creator"])
logger = logging.getLogger(__name__)


# Регистр активных asyncio-задач для возможности cancel.
_active_tasks: dict[str, asyncio.Task] = {}


def _register_task(task_id: str, async_task: asyncio.Task) -> None:
    _active_tasks[task_id] = async_task
    async_task.add_done_callback(lambda _t: _active_tasks.pop(task_id, None))


def _make_browser_client() -> BrowserAgentClient:
    settings = get_settings()
    config = BrowserAgentConfig(
        vision_x_token=settings.vision_x_token,
        vision_api_url=settings.vision_api_url,
        vision_profile_id=settings.vision_profile_id,
    )
    return BrowserAgentClient(config)


async def _set_task_status(
    task_id: str,
    status: CampaignCreatorTaskStatus,
    *,
    step: str | None = None,
    data: dict | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(CampaignCreatorTask).where(CampaignCreatorTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            logger.warning("Задача campaign_creator не найдена: %s", task_id)
            return
        task.status = status
        if status == CampaignCreatorTaskStatus.RUNNING:
            # Сбрасываем прошлую ошибку, чтобы UI не показывал красный баннер.
            task.error_message = None
        if step is not None:
            task.current_step = step
        if data is not None:
            if status == CampaignCreatorTaskStatus.FAILED:
                task.error_message = data.get("error")
            else:
                task.checkpoint_data = data
        await db.commit()


async def _load_task(task_id: str) -> CampaignCreatorTask:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(CampaignCreatorTask).where(CampaignCreatorTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return task


async def _load_offer(offer_code: str) -> Offer | None:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Offer).where(Offer.code == offer_code))
        return result.scalar_one_or_none()


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
        context_json=task.context_json,
    )


async def _run_steps_for_task(task_id: str, steps: list[BaseStep]) -> None:
    """Подключиться к браузеру и выполнить заданный список шагов на задаче."""

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    task = await _load_task(task_id)
    if not task.context_json:
        await set_status(
            CampaignCreatorTaskStatus.FAILED,
            data={"error": "context_json пуст — нельзя запустить шаги"},
        )
        return
    try:
        context = context_from_dict(task.context_json)
    except Exception as exc:
        logger.exception("Не удалось разобрать context_json для задачи %s", task_id)
        await set_status(CampaignCreatorTaskStatus.FAILED, data={"error": str(exc)})
        return

    client = _make_browser_client()
    try:
        async with open_page(client) as page:
            await execute_steps(steps, page, context, set_status)
    except asyncio.CancelledError:
        logger.warning("Задача campaign_creator %s отменена пользователем", task_id)
        await set_status(
            CampaignCreatorTaskStatus.FAILED,
            data={"error": "Остановлено пользователем"},
        )
        raise
    except Exception as exc:
        logger.error("Критическая ошибка campaign_creator %s: %s", task_id, exc)
        await set_status(CampaignCreatorTaskStatus.FAILED, data={"error": str(exc)})


# === Endpoints =============================================================


@router.get("/steps", response_model=CampaignCreatorStepsListSchema)
async def list_steps() -> CampaignCreatorStepsListSchema:
    """Список всех шагов в каноничном порядке."""
    return CampaignCreatorStepsListSchema(
        steps=[
            CampaignCreatorStepInfoSchema(name=n, idempotent=step_idempotent(n))
            for n in STEPS_ORDER
        ]
    )


@router.post("/start", response_model=CampaignCreatorTaskSchema)
async def start_campaign_creator(body: CampaignCreatorStartRequestSchema):
    """Создать задачу автосоздания и запустить полный пайплайн в фоне."""
    offer = await _load_offer(body.offer_code)
    if offer is None:
        raise HTTPException(status_code=404, detail=f"Оффер не найден: {body.offer_code}")

    missing = [
        f
        for f in ("cabinet_id", "pixel_id", "landing_url", "geo_slot_name")
        if not getattr(offer, f)
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"У оффера {offer.code} не заполнены поля: {', '.join(missing)}",
        )

    campaign_name = build_campaign_name(
        iter_num=body.iter_num,
        geo_code=offer.geo_code or offer.code,
    )

    context = StepContext(
        offer_code=offer.code,
        cabinet_id=offer.cabinet_id,
        campaign_name=campaign_name,
        pixel_id=offer.pixel_id,
        landing_url=offer.landing_url,
        geo_code=offer.geo_code or "",
        geo_slot_name=offer.geo_slot_name,
        daily_budget=body.daily_budget,
        attribution_days=body.attribution_days,
        budget_level=body.budget_level,
        iter_num=body.iter_num,
        adsets=[AdsetSpec(**a.model_dump()) for a in body.adsets],
        creo_folder=body.creo_folder,
        extra={"offer_country_name": offer.country_name or ""},
    )

    factory = get_session_factory()
    async with factory() as db:
        task = CampaignCreatorTask(
            offer_code=body.offer_code,
            creative_folder=body.creo_folder,
            cabinet_id=offer.cabinet_id,
            status=CampaignCreatorTaskStatus.PENDING,
            campaign_name=campaign_name,
            context_json=context_to_dict(context),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = str(task.id)

    _register_task(task_id, asyncio.create_task(_run_steps_for_task(task_id, build_pipeline())))

    return _task_to_schema(task)


@router.post("/{task_id}/run-step/{step_name}", response_model=CampaignCreatorTaskSchema)
async def run_single_step(task_id: str, step_name: str) -> CampaignCreatorTaskSchema:
    """Выполнить ровно один шаг на текущей странице задачи."""
    if step_name not in STEPS_ORDER:
        raise HTTPException(status_code=400, detail=f"Неизвестный шаг: {step_name}")
    task = await _load_task(task_id)
    if task.status == CampaignCreatorTaskStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Задача уже выполняется")
    # Сразу переводим в RUNNING, чтобы UI не залипал на FAILED до старта корутины.
    await _set_task_status(task_id, CampaignCreatorTaskStatus.RUNNING, step=step_name)
    _register_task(
        task_id, asyncio.create_task(_run_steps_for_task(task_id, [build_step(step_name)]))
    )
    return _task_to_schema(await _load_task(task_id))


@router.post("/{task_id}/run-from/{step_name}", response_model=CampaignCreatorTaskSchema)
async def run_from_step(task_id: str, step_name: str) -> CampaignCreatorTaskSchema:
    """Запустить пайплайн начиная с указанного шага до конца."""
    if step_name not in STEPS_ORDER:
        raise HTTPException(status_code=400, detail=f"Неизвестный шаг: {step_name}")
    task = await _load_task(task_id)
    if task.status == CampaignCreatorTaskStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Задача уже выполняется")
    await _set_task_status(task_id, CampaignCreatorTaskStatus.RUNNING, step=step_name)
    _register_task(
        task_id,
        asyncio.create_task(_run_steps_for_task(task_id, build_pipeline(start_from=step_name))),
    )
    return _task_to_schema(await _load_task(task_id))


@router.post("/{task_id}/resume", response_model=CampaignCreatorTaskSchema)
async def resume_task(task_id: str) -> CampaignCreatorTaskSchema:
    """Продолжить упавшую задачу с упавшего шага."""
    task = await _load_task(task_id)
    if task.status != CampaignCreatorTaskStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Resume доступен только для FAILED, текущий статус: {task.status.value}",
        )
    if not task.current_step:
        raise HTTPException(status_code=409, detail="current_step пуст — некуда возобновлять")
    await _set_task_status(task_id, CampaignCreatorTaskStatus.RUNNING, step=task.current_step)
    _register_task(
        task_id,
        asyncio.create_task(
            _run_steps_for_task(task_id, build_pipeline(start_from=task.current_step))
        ),
    )
    return _task_to_schema(await _load_task(task_id))


@router.post("/{task_id}/cancel", response_model=CampaignCreatorTaskSchema)
async def cancel_task(task_id: str) -> CampaignCreatorTaskSchema:
    """Принудительно остановить выполняющуюся задачу."""
    async_task = _active_tasks.get(task_id)
    if async_task is None or async_task.done():
        # На случай "висящего" статуса RUNNING без живой asyncio-задачи —
        # переведём в FAILED, чтобы UI разблокировался.
        task = await _load_task(task_id)
        if task.status == CampaignCreatorTaskStatus.RUNNING:
            await _set_task_status(
                task_id,
                CampaignCreatorTaskStatus.FAILED,
                data={"error": "Остановлено пользователем"},
            )
            task = await _load_task(task_id)
        return _task_to_schema(task)

    async_task.cancel()
    try:
        await asyncio.wait_for(async_task, timeout=5)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    task = await _load_task(task_id)
    return _task_to_schema(task)


@router.get("/{task_id}/status", response_model=CampaignCreatorTaskSchema)
async def get_task_status(task_id: str):
    """Получить текущий статус задачи."""
    task = await _load_task(task_id)
    return _task_to_schema(task)
