# -*- coding: utf-8 -*-
"""API роутер для автоматического создания кампаний в Ads Manager."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from apps.api.schemas import CampaignCreatorStartRequestSchema, CampaignCreatorTaskSchema
from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import StepContext
from core.campaign_creator.steps.create_campaign import CreateCampaignStep
from core.campaign_creator.steps.set_geo import SetGeoStep
from core.db import get_session_factory
from core.domain import CampaignCreatorTaskStatus
from core.models import CampaignCreatorTask

router = APIRouter(prefix="/api/campaign-creator", tags=["campaign-creator"])
logger = logging.getLogger(__name__)


async def _set_task_status(
    task_id: str,
    status: CampaignCreatorTaskStatus,
    *,
    step: str | None = None,
    data: dict | None = None,
) -> None:
    """Обновляет статус задачи в БД через новую сессию (безопасно для background tasks)."""
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
        if step is not None:
            task.current_step = step
        if data is not None:
            if status == CampaignCreatorTaskStatus.FAILED:
                task.error_message = data.get("error")
            else:
                task.checkpoint_data = data
        await db.commit()


async def _run_creator(task_id: str, context: StepContext) -> None:
    """Фоновая задача: выполняет шаги создания кампании через CDP."""
    from playwright.async_api import async_playwright

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    steps = [CreateCampaignStep(), SetGeoStep()]
    runner = CampaignCreatorRunner(steps=steps, set_status=set_status)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(context.cdp_url)
            pages = browser.contexts[0].pages if browser.contexts else []
            page = pages[0] if pages else await browser.new_page()
            await runner.run_until_checkpoint(page, context)
            await browser.close()
    except Exception as exc:
        logger.error("Критическая ошибка в campaign_creator task %s: %s", task_id, exc)
        await _set_task_status(
            task_id,
            CampaignCreatorTaskStatus.FAILED,
            data={"error": str(exc)},
        )


async def _continue_creator(task_id: str, context: StepContext, start_index: int) -> None:
    """Продолжение выполнения после подтверждения checkpoint."""
    from playwright.async_api import async_playwright

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    steps = [CreateCampaignStep(), SetGeoStep()]
    runner = CampaignCreatorRunner(steps=steps, set_status=set_status)
    runner._current_index = start_index

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(context.cdp_url)
            pages = browser.contexts[0].pages if browser.contexts else []
            page = pages[0] if pages else await browser.new_page()
            await runner.run_until_checkpoint(page, context)
            await browser.close()
    except Exception as exc:
        logger.error("Ошибка при продолжении campaign_creator task %s: %s", task_id, exc)
        await _set_task_status(
            task_id,
            CampaignCreatorTaskStatus.FAILED,
            data={"error": str(exc)},
        )


@router.post("/start", response_model=CampaignCreatorTaskSchema)
async def start_campaign_creator(body: CampaignCreatorStartRequestSchema):
    """Создать задачу автосоздания кампании и запустить её в фоне."""
    factory = get_session_factory()

    # Генерируем имя кампании: простой формат без planner
    campaign_name = f"{body.offer_code.upper()} | AUTO"

    async with factory() as db:
        task = CampaignCreatorTask(
            offer_code=body.offer_code,
            creative_folder=body.creative_folder,
            cabinet_id=body.cabinet_id,
            cdp_url=body.cdp_url,
            status=CampaignCreatorTaskStatus.PENDING,
            campaign_name=campaign_name,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = str(task.id)

    context = StepContext(
        offer_code=body.offer_code,
        creative_folder=body.creative_folder,
        cabinet_id=body.cabinet_id,
        campaign_name=campaign_name,
        cdp_url=body.cdp_url,
        extra={},
    )
    asyncio.create_task(_run_creator(task_id, context))

    return CampaignCreatorTaskSchema(
        id=task_id,
        status=CampaignCreatorTaskStatus.PENDING.value,
        current_step=None,
        checkpoint_data=None,
        error_message=None,
        campaign_name=campaign_name,
        offer_code=body.offer_code,
        created_at=task.created_at.isoformat(),
    )


@router.post("/{task_id}/confirm", response_model=CampaignCreatorTaskSchema)
async def confirm_checkpoint(task_id: str):
    """Подтвердить checkpoint и продолжить выполнение шагов."""
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(CampaignCreatorTask).where(CampaignCreatorTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if task.status != CampaignCreatorTaskStatus.WAITING_CONFIRMATION:
            raise HTTPException(
                status_code=400,
                detail=f"Задача не ожидает подтверждения (статус: {task.status})",
            )
        task.status = CampaignCreatorTaskStatus.CONFIRMED
        await db.commit()

        # Определяем индекс шага: шаги после checkpoint (create_campaign — индекс 0)
        all_step_names = ["create_campaign", "set_geo"]
        current_step_name = task.current_step or ""
        try:
            step_index = all_step_names.index(current_step_name) + 1
        except ValueError:
            step_index = 1

        context = StepContext(
            offer_code=task.offer_code,
            creative_folder=task.creative_folder,
            cabinet_id=task.cabinet_id,
            campaign_name=task.campaign_name or f"{task.offer_code.upper()} | AUTO",
            cdp_url=task.cdp_url,
            extra={},
        )

        asyncio.create_task(_continue_creator(task_id, context, step_index))

        return CampaignCreatorTaskSchema(
            id=str(task.id),
            status=CampaignCreatorTaskStatus.CONFIRMED.value,
            current_step=task.current_step,
            checkpoint_data=task.checkpoint_data,
            error_message=task.error_message,
            campaign_name=task.campaign_name,
            offer_code=task.offer_code,
            created_at=task.created_at.isoformat(),
        )


@router.get("/{task_id}/status", response_model=CampaignCreatorTaskSchema)
async def get_task_status(task_id: str):
    """Получить текущий статус задачи автосоздания."""
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(CampaignCreatorTask).where(CampaignCreatorTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
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
