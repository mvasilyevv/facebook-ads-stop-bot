# -*- coding: utf-8 -*-
"""API роутер для автоматического создания кампаний в Ads Manager."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from playwright.async_api import async_playwright
from sqlalchemy import select

from apps.api.schemas import CampaignCreatorStartRequestSchema, CampaignCreatorTaskSchema
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import StepContext
from core.campaign_creator.steps.create_campaign import CreateCampaignStep
from core.campaign_creator.steps.set_geo import SetGeoStep
from core.config import get_settings
from core.db import get_session_factory
from core.domain import CampaignCreatorTaskStatus
from core.models import CampaignCreatorTask, Offer

router = APIRouter(prefix="/api/campaign-creator", tags=["campaign-creator"])
logger = logging.getLogger(__name__)


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


async def _load_offer_country_name(offer_code: str) -> str:
    """Загружает country_name оффера из БД по коду. Возвращает пустую строку если не найден."""
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(Offer).where(Offer.code == offer_code)
        )
        offer = result.scalar_one_or_none()
        if offer is None or not offer.country_name:
            logger.warning("Оффер %s не найден или не имеет country_name", offer_code)
            return ""
        return offer.country_name


async def _run_creator(task_id: str, context: StepContext) -> None:
    """Фоновая задача: запускает Vision-профиль и выполняет шаги создания кампании."""

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    country_name = await _load_offer_country_name(context.offer_code)
    context = StepContext(
        offer_code=context.offer_code,
        creative_folder=context.creative_folder,
        cabinet_id=context.cabinet_id,
        campaign_name=context.campaign_name,
        extra={**context.extra, "offer_country_name": country_name},
    )

    steps = [CreateCampaignStep(), SetGeoStep()]
    runner = CampaignCreatorRunner(steps=steps, set_status=set_status)

    client = _make_browser_client()
    try:
        await client.start()
        await client.start_browser()
        cdp_url = client.cdp_url
        if not cdp_url:
            raise RuntimeError("Vision не вернул cdp_port после старта браузера")

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
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
    finally:
        await client.disconnect_browser()
        await client.close()


async def _continue_creator(task_id: str, context: StepContext, start_index: int) -> None:
    """Продолжение выполнения после подтверждения checkpoint."""

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    if not context.extra.get("offer_country_name"):
        country_name = await _load_offer_country_name(context.offer_code)
        context = StepContext(
            offer_code=context.offer_code,
            creative_folder=context.creative_folder,
            cabinet_id=context.cabinet_id,
            campaign_name=context.campaign_name,
            extra={**context.extra, "offer_country_name": country_name},
        )

    steps = [CreateCampaignStep(), SetGeoStep()]
    runner = CampaignCreatorRunner(steps=steps, set_status=set_status)
    runner._current_index = start_index

    client = _make_browser_client()
    try:
        await client.start()
        await client.start_browser()
        cdp_url = client.cdp_url
        if not cdp_url:
            raise RuntimeError("Vision не вернул cdp_port после старта браузера")

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
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
    finally:
        await client.disconnect_browser()
        await client.close()


@router.post("/start", response_model=CampaignCreatorTaskSchema)
async def start_campaign_creator(body: CampaignCreatorStartRequestSchema):
    """Создать задачу автосоздания кампании и запустить её в фоне."""
    factory = get_session_factory()
    campaign_name = f"{body.offer_code.upper()} | AUTO"

    async with factory() as db:
        task = CampaignCreatorTask(
            offer_code=body.offer_code,
            creative_folder=body.creative_folder,
            cabinet_id=body.cabinet_id,
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
            select(CampaignCreatorTask)
            .where(CampaignCreatorTask.id == task_id)
            .with_for_update()
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
