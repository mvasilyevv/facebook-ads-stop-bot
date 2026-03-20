from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import ApiStateDep
from apps.api.schemas.ads import AdActionResponse, AdBlockRequest, AdDetail, AdSummary

router = APIRouter(prefix="/ads", tags=["ads"])


@router.get("", response_model=list[AdSummary])
async def list_ads(api_state: ApiStateDep) -> list[AdSummary]:
    return api_state.ads_service.list_ads()


@router.get("/{fb_ad_id}", response_model=AdDetail)
async def get_ad(fb_ad_id: str, api_state: ApiStateDep) -> AdDetail:
    ad = api_state.ads_service.get_ad(fb_ad_id)
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    return ad


@router.post("/{fb_ad_id}/block", response_model=AdActionResponse)
async def block_ad(
    fb_ad_id: str, payload: AdBlockRequest, api_state: ApiStateDep
) -> AdActionResponse:
    result = api_state.ads_service.block_ad(fb_ad_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    return result


@router.post("/{fb_ad_id}/unblock", response_model=AdActionResponse)
async def unblock_ad(fb_ad_id: str, api_state: ApiStateDep) -> AdActionResponse:
    result = api_state.ads_service.unblock_ad(fb_ad_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    return result
