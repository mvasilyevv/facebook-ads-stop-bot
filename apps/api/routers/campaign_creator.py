# -*- coding: utf-8 -*-
"""API роутер для автоматического создания кампаний в Ads Manager (full autopilot)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from playwright.async_api import async_playwright
from sqlalchemy import select

from apps.api.schemas import CampaignCreatorStartRequestSchema, CampaignCreatorTaskSchema
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.campaign_creator.naming import build_campaign_name
from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import AdsetSpec, StepContext
from core.campaign_creator.steps.create_adset import CreateAdsetStep
from core.campaign_creator.steps.create_campaign import CreateCampaignStep
from core.campaign_creator.steps.fill_texts import FillTextsStep
from core.campaign_creator.steps.save_draft import SaveDraftStep
from core.campaign_creator.steps.set_attribution import SetAttributionStep
from core.campaign_creator.steps.set_budget import SetBudgetStep
from core.campaign_creator.steps.set_cta import SetCtaStep
from core.campaign_creator.steps.set_geo import SetGeoStep
from core.campaign_creator.steps.set_pixel_event import SetPixelEventStep
from core.campaign_creator.steps.set_tracking_url import SetTrackingUrlStep
from core.campaign_creator.steps.upload_creatives import UploadCreativesStep
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


def _build_steps() -> list:
    """Полный pipeline создания кампании — порядок критичен."""
    return [
        CreateCampaignStep(),
        SetBudgetStep(),
        SetAttributionStep(),
        CreateAdsetStep(),
        SetPixelEventStep(),
        SetGeoStep(),
        UploadCreativesStep(),
        FillTextsStep(),
        SetCtaStep(),
        SetTrackingUrlStep(),
        SaveDraftStep(),
    ]


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
        if step is not None:
            task.current_step = step
        if data is not None:
            if status == CampaignCreatorTaskStatus.FAILED:
                task.error_message = data.get("error")
            else:
                task.checkpoint_data = data
        await db.commit()


async def _load_offer(offer_code: str) -> Offer | None:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Offer).where(Offer.code == offer_code))
        return result.scalar_one_or_none()


async def _run_creator(task_id: str, context: StepContext) -> None:
    """Фоновая задача — запуск pipeline до 'Сохранить как черновик'."""

    async def set_status(status, *, step=None, data=None):
        await _set_task_status(task_id, status, step=step, data=data)

    runner = CampaignCreatorRunner(steps=_build_steps(), set_status=set_status)

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
            await runner.run_all(page, context)
            await browser.close()
    except Exception as exc:
        logger.error("Критическая ошибка campaign_creator %s: %s", task_id, exc)
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
    """Создать задачу автосоздания и запустить её в фоне (full autopilot)."""
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

    campaign_name = build_campaign_name(iter_num=body.iter_num, offer_code=offer.code)

    factory = get_session_factory()
    async with factory() as db:
        task = CampaignCreatorTask(
            offer_code=body.offer_code,
            creative_folder=body.creo_folder,
            cabinet_id=offer.cabinet_id,
            status=CampaignCreatorTaskStatus.PENDING,
            campaign_name=campaign_name,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = str(task.id)

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


@router.get("/{task_id}/status", response_model=CampaignCreatorTaskSchema)
async def get_task_status(task_id: str):
    """Получить текущий статус задачи."""
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
