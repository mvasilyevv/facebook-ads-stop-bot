from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.ads import AdActionResponse, AdBlockRequest, AdDetail, AdSummary
from core.domain import DecisionType, EntityType, ScopePresence, TrackingMode
from core.repositories import AdsRepository, ControlFlagsRepository, OffersRepository

router = APIRouter(prefix="/ads", tags=["ads"])


async def _resolve_ad_cpa(repo: OffersRepository, ad) -> object:
    offer = await repo.resolve_offer_for_ad(
        ad_name=ad.name,
        ad_id=ad.fb_ad_id,
        adset_scope_key=ad.adset.scope_key,
    )
    if offer is None:
        return None
    rate = await repo.resolve_rate_version(offer.id, datetime.now(tz=UTC))
    if rate is None:
        return None
    return rate.cpa_usd


async def _map_ad_summary(ad, offers_repo: OffersRepository) -> AdSummary:
    return AdSummary(
        fb_ad_id=ad.fb_ad_id,
        campaign_name=ad.campaign.name,
        adset_name=ad.adset.name,
        ad_name=ad.name,
        delivery_status=ad.delivery_status.value,
        tracking_mode=ad.tracking_mode.value,
        scope_presence=ad.scope_presence.value,
        last_seen_at=ad.last_seen_at,
        last_decision=ad.last_decision.value,
        resolved_cpa_usd=await _resolve_ad_cpa(offers_repo, ad),
    )


async def _map_ad_detail(ad, offers_repo: OffersRepository) -> AdDetail:
    return AdDetail(
        **(await _map_ad_summary(ad, offers_repo)).model_dump(),
        campaign_scope_key=ad.campaign.scope_key,
        adset_scope_key=ad.adset.scope_key,
        last_scan_run_id=str(ad.last_scan_run_id) if ad.last_scan_run_id is not None else None,
        created_at=ad.created_at,
        updated_at=ad.updated_at,
    )


@router.get("", response_model=list[AdSummary])
async def list_ads(session: DbSessionDep) -> list[AdSummary]:
    ads_repo = AdsRepository(session)
    offers_repo = OffersRepository(session)
    ads = await ads_repo.list_ads()
    return [await _map_ad_summary(ad, offers_repo) for ad in ads]


@router.get("/{fb_ad_id}", response_model=AdDetail)
async def get_ad(fb_ad_id: str, session: DbSessionDep) -> AdDetail:
    ads_repo = AdsRepository(session)
    offers_repo = OffersRepository(session)
    ad = await ads_repo.get_ad_by_fb_id(fb_ad_id)
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    return await _map_ad_detail(ad, offers_repo)


@router.post("/{fb_ad_id}/block", response_model=AdActionResponse)
async def block_ad(
    fb_ad_id: str, payload: AdBlockRequest, session: DbSessionDep
) -> AdActionResponse:
    ads_repo = AdsRepository(session)
    flags_repo = ControlFlagsRepository(session)
    offers_repo = OffersRepository(session)

    now = datetime.now(tz=UTC)
    ad = await ads_repo.update_ad_review_state(
        fb_ad_id,
        tracking_mode=TrackingMode.MANUAL_BLOCK,
        last_decision=DecisionType.SKIPPED_BY_POLICY,
        last_action_source=payload.created_by,
        last_action_at=now,
    )
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    await flags_repo.upsert_control_flag(
        entity_type=EntityType.AD,
        entity_id=fb_ad_id,
        reason=payload.reason,
        created_by=payload.created_by,
        tracking_mode=TrackingMode.MANUAL_BLOCK,
    )
    refreshed_ad = await ads_repo.get_ad_by_fb_id(fb_ad_id)
    await session.commit()
    if refreshed_ad is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось перечитать объявление после блокировки",
        )
    return AdActionResponse(
        message="Объявление переведено в ручную блокировку",
        ad=await _map_ad_detail(refreshed_ad, offers_repo),
    )


@router.post("/{fb_ad_id}/unblock", response_model=AdActionResponse)
async def unblock_ad(fb_ad_id: str, session: DbSessionDep) -> AdActionResponse:
    ads_repo = AdsRepository(session)
    flags_repo = ControlFlagsRepository(session)
    offers_repo = OffersRepository(session)

    ad = await ads_repo.update_ad_review_state(
        fb_ad_id,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_decision=DecisionType.NO_ACTION,
        last_action_source="operator",
        last_action_at=datetime.now(tz=UTC),
    )
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Объявление не найдено")
    await flags_repo.delete_control_flag(EntityType.AD, fb_ad_id)
    refreshed_ad = await ads_repo.get_ad_by_fb_id(fb_ad_id)
    await session.commit()
    if refreshed_ad is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось перечитать объявление после разблокировки",
        )
    return AdActionResponse(
        message="Объявление возвращено в отслеживание",
        ad=await _map_ad_detail(refreshed_ad, offers_repo),
    )
