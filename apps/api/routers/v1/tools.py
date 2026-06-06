# -*- coding: utf-8 -*-
"""Роутер Tools: уникализация креативов, открытие папки, campaign-create.

Разделение dev-only vs prod-safe:
- creative-uniquify и open-folder — DEV-ONLY (работают с FS, открывают Finder).
  Нет смысла на удалённом/prod-сервере → закрыты require_dev_tools (DEV_TOOLS_ENABLED).
- campaign-create/folders и campaign-create/plan — PROD-SAFE: читают структуру
  папки с креативами на той же машине, что и сервер. Mini App Scripts-экрана
  использует эти ручки в проде (сервер и файлы на одной машине по дизайну проекта).
  Валидация путей через default_creatives_root() сохранена — защита от path traversal.

Endpoints:
    POST /tools/creative-uniquify          — уникализация (DEV-ONLY)
    POST /tools/creative-uniquify/open-folder — открыть Finder (DEV-ONLY)
    GET  /tools/campaign-create/folders    — список папок (prod-safe)
    POST /tools/campaign-create/plan       — план кампании (prod-safe)
"""

from __future__ import annotations

import logging
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.params import File, Form

from apps.api.deps import DepSettings
from apps.api.routers.v1.schemas.tools import (
    CampaignFolderItem,
    CampaignPlanRequest,
    CampaignScriptPlanOut,
    CreativeUniquifyResponse,
    OpenFolderRequest,
)
from core.campaign_scripts.creative_folder import (
    CampaignCreativeValidationError,
    inspect_creative_folder,
    list_creative_folders,
)
from core.campaign_scripts.planner import (
    CampaignScriptConfig,
    CampaignScriptPlanError,
    build_campaign_script_plan,
)
from core.creatives.folder_opener import CreativeFolderOpenError, open_generated_folder
from core.creatives.service import (
    MAX_COPY_COUNT,
    CreativeInput,
    CreativeValidationError,
    uniquify_creatives,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tools"])

# Максимальный суммарный размер загружаемых файлов: 200 МБ
_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def require_dev_tools(settings: DepSettings) -> None:
    """FastAPI-dependency: блокирует dev-only endpoints в продакшене.

    Если DEV_TOOLS_ENABLED не выставлен в true — возвращает 403.
    Используй как: Depends(require_dev_tools)
    """
    if not settings.dev_tools_enabled:
        raise HTTPException(
            status_code=403,
            detail="dev-only endpoint, set DEV_TOOLS_ENABLED=true",
        )


@router.post("/tools/creative-uniquify", response_model=CreativeUniquifyResponse)
async def creative_uniquify(
    offer_name: str = Form(...),
    copies: int = Form(...),
    files: list[UploadFile] = File(...),
    _: None = Depends(require_dev_tools),
) -> CreativeUniquifyResponse:
    """Уникализирует загруженные изображения и сохраняет в FB_Agent_Creo.

    Принимает form-data с полями offer_name, copies и files (multipart).
    Не удаляет выходную папку — клиент может открыть её через open-folder.
    """
    # Валидация copies
    if copies < 1 or copies > MAX_COPY_COUNT:
        raise HTTPException(
            status_code=422,
            detail=f"copies должно быть от 1 до {MAX_COPY_COUNT}",
        )

    if not files:
        raise HTTPException(status_code=422, detail="Нужен хотя бы один файл")

    # Читаем все файлы и проверяем суммарный размер
    creatives: list[CreativeInput] = []
    total_bytes = 0
    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Суммарный размер файлов превышает 200 МБ",
            )
        creatives.append(CreativeInput(filename=upload.filename or "", content=content))

    # Запускаем уникализацию
    t0 = time.monotonic()
    try:
        result = await uniquify_creatives(
            offer_name=offer_name,
            copies=copies,
            creatives=creatives,
        )
    except CreativeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    duration_ms = int((time.monotonic() - t0) * 1000)

    return CreativeUniquifyResponse(
        output_dir=result.iteration_dir,
        iteration_name=result.iteration_name,
        files_created=len(result.files),
        creative_count=result.creative_count,
        copy_count=result.copy_count,
        duration_ms=duration_ms,
    )


@router.post("/tools/creative-uniquify/open-folder", status_code=200)
async def open_creative_folder(
    body: OpenFolderRequest,
    _: None = Depends(require_dev_tools),
) -> dict:
    """Открывает папку с результатом уникализации в Finder (macOS) или xdg-open (Linux).

    ПРЕДУПРЕЖДЕНИЕ: dev-only endpoint. На удалённом/prod-сервере не имеет смысла,
    так как открывает Finder на сервере, а не у пользователя.

    Безопасность: разрешает открывать только папки внутри корня FB_Agent_Creo.
    """
    try:
        await open_generated_folder(body.path)
    except CreativeFolderOpenError as exc:
        # CreativeFolderOpenError содержит 2 варианта: не найдена → 404, вне корня → 403
        msg = str(exc)
        if "только папки внутри" in msg or "только внутри" in msg:
            raise HTTPException(status_code=403, detail=msg) from exc
        raise HTTPException(status_code=404, detail=msg) from exc

    return {}


@router.get("/tools/campaign-create/folders", response_model=list[CampaignFolderItem])
async def get_campaign_creative_folders() -> list[CampaignFolderItem]:
    """Возвращает список папок с креативами из корня FB_Agent_Creo.

    Prod-safe: читает структуру FS без открытия GUI или записи файлов.
    Сканирует 1 уровень глубины. Возвращает пустой список если корня нет.
    Используется Mini App Scripts-экраном.
    """
    summaries = await list_creative_folders(limit=100)
    return [
        CampaignFolderItem(
            name=s.name,
            path=s.path,
            adset_count=s.adset_count,
            creative_count=s.creative_count,
            media_type=s.media_type,
            updated_at=s.updated_at,
            is_valid=s.is_valid,
            validation_error=s.validation_error,
        )
        for s in summaries
    ]


@router.post("/tools/campaign-create/plan", response_model=CampaignScriptPlanOut)
async def build_campaign_plan(
    body: CampaignPlanRequest,
) -> CampaignScriptPlanOut:
    """Строит план создания кампании из папки с креативами и настроек UI.

    Читает структуру папки, валидирует файлы, собирает имена кампании/групп/объявлений,
    URL-параметры и ручной чек-лист.
    """
    # Разбираем дату генерации если передана
    generation_date: date | None = None
    if body.generation_date:
        try:
            generation_date = date.fromisoformat(body.generation_date)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="generation_date должна быть в формате YYYY-MM-DD",
            ) from exc

    # Читаем и валидируем папку с креативами
    try:
        folder = await inspect_creative_folder(body.folder_name)
    except CampaignCreativeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    config = CampaignScriptConfig(
        offer_code=body.offer_code,
        offer_country_name=body.offer_country_name,
        cabinet_id=body.cabinet_id,
        sub2=body.sub2,
        generation_date=generation_date,
    )

    try:
        plan = build_campaign_script_plan(folder=folder, config=config)
    except CampaignScriptPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Конвертируем dataclass в Pydantic-схему
    return CampaignScriptPlanOut(
        campaign_name=plan.campaign_name,
        offer_code=plan.offer_code,
        offer_country_name=plan.offer_country_name,
        creative_folder_name=plan.creative_folder_name,
        creative_folder_path=plan.creative_folder_path,
        conversion_event=plan.conversion_event,
        cabinet_id=plan.cabinet_id,
        sub2=plan.sub2,
        media_type=plan.media_type,
        adset_count=plan.adset_count,
        ad_count=plan.ad_count,
        adsets=[
            {
                "name": adset.name,
                "folder_path": adset.folder_path,
                "ads": [
                    {
                        "name": ad.name,
                        "media_file_name": ad.media_file_name,
                        "media_search_name": ad.media_search_name,
                        "media_path": ad.media_path,
                        "media_type": ad.media_type,
                        "url_params": ad.url_params,
                    }
                    for ad in adset.ads
                ],
            }
            for adset in plan.adsets
        ],
        location_plan={
            "add_locations": plan.location_plan.add_locations,
            "offer_country_name": plan.location_plan.offer_country_name,
            "required_location_type": plan.location_plan.required_location_type,
            "remove_initial_location_after_add": plan.location_plan.remove_initial_location_after_add,
            "rejected_location_terms": plan.location_plan.rejected_location_terms,
        },
        manual_guide=[
            {
                "title": section.title,
                "items": [
                    {
                        "label": item.label,
                        "value": item.value,
                        "copyable": item.copyable,
                    }
                    for item in section.items
                ],
            }
            for section in plan.manual_guide
        ],
        safety_notes=plan.safety_notes,
    )
