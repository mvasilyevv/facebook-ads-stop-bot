# -*- coding: utf-8 -*-
"""FastAPI роутер ручного помощника создания кампаний из папок креативов."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    CampaignCreativeFolderSchema,
    CampaignScriptPlanRequestSchema,
    CampaignScriptPlanSchema,
)
from core.campaign_scripts import (
    CampaignCreativeValidationError,
    CampaignScriptConfig,
    build_campaign_script_plan,
    inspect_creative_folder,
    list_creative_folders,
)
from core.campaign_scripts.planner import CampaignScriptPlanError
from core.models import Offer

router = APIRouter(prefix="/api", tags=["campaign-scripts"])


@router.get(
    "/tools/campaign-create/folders",
    response_model=list[CampaignCreativeFolderSchema],
)
async def list_campaign_creative_folders(limit: int = Query(default=100, ge=1, le=300)):
    """Вернуть валидные папки креативов для выбора в UI."""
    folders = await list_creative_folders(limit=limit)
    return [CampaignCreativeFolderSchema(**asdict(folder)) for folder in folders]


@router.post("/tools/campaign-create/plan", response_model=CampaignScriptPlanSchema)
async def build_campaign_create_plan(
    body: CampaignScriptPlanRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    """Построить безопасный план и ручной чек-лист без действий в браузере."""
    result = await db.execute(
        select(Offer).where(func.lower(Offer.code) == body.offer_code.casefold())
    )
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    if not offer.country_name:
        raise HTTPException(
            status_code=400,
            detail="У оффера не указана страна. Заполните страну на странице оффера",
        )

    try:
        folder = await inspect_creative_folder(body.creative_folder_name)
        plan = build_campaign_script_plan(
            folder=folder,
            config=CampaignScriptConfig(
                offer_code=body.offer_code,
                offer_country_name=offer.country_name,
                cabinet_id=body.cabinet_id,
            ),
        )
    except (CampaignCreativeValidationError, CampaignScriptPlanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CampaignScriptPlanSchema(**asdict(plan))
