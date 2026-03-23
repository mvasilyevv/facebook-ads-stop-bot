from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.common import ActionJobStatus, FastStopState, RiskBand
from apps.api.schemas.watchlist import WatchlistItem
from core.domain import DeliveryStatus
from core.repositories import (
    ActionJobsRepository,
    AdsRepository,
    BrowserRepository,
    WatchlistRepository,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItem], status_code=status.HTTP_200_OK)
async def list_watchlist(
    session: DbSessionDep,
    profile_id: str | None = Query(default=None),
) -> list[WatchlistItem]:
    resolved_profile_id = None
    if profile_id is not None:
        profile = await BrowserRepository(session).get_profile_by_vendor_id(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Профиль `{profile_id}` не найден",
            )
        resolved_profile_id = str(profile.id)

    repo = WatchlistRepository(session)
    entries = await repo.list_entries(limit=200)
    if resolved_profile_id is not None:
        entries = [entry for entry in entries if str(entry.profile_id) == resolved_profile_id]

    fb_ad_ids = [entry.fb_ad_id for entry in entries]
    ads = await AdsRepository(session).get_ads_by_fb_ad_ids(fb_ad_ids)
    action_jobs = await ActionJobsRepository(session).get_latest_jobs(fb_ad_ids)
    browser_repo = BrowserRepository(session)

    profile_cache: dict[str, str] = {}
    browser_host_cache: dict[str, str] = {}
    items: list[WatchlistItem] = []
    for entry in entries:
        profile_value = await _resolve_profile_value(
            browser_repo=browser_repo,
            cache=profile_cache,
            profile_id=str(entry.profile_id) if entry.profile_id is not None else None,
        )
        browser_host_value = await _resolve_browser_host_value(
            browser_repo=browser_repo,
            cache=browser_host_cache,
            browser_host_id=(
                str(entry.browser_host_id) if entry.browser_host_id is not None else None
            ),
        )
        ad = ads.get(entry.fb_ad_id)
        action_job = action_jobs.get(entry.fb_ad_id)
        items.append(
            WatchlistItem(
                id=str(entry.id),
                fb_ad_id=entry.fb_ad_id,
                profile_id=profile_value,
                browser_host_id=browser_host_value,
                campaign_name=getattr(getattr(ad, "campaign", None), "name", None),
                adset_name=getattr(getattr(ad, "adset", None), "name", None),
                ad_name=getattr(ad, "name", None),
                risk_band=entry.risk_band.value,
                fast_stop_state=_resolve_fast_stop_state(
                    risk_band=entry.risk_band,
                    ad=ad,
                    action_job=action_job,
                ),
                watch_reason=entry.last_reason,
                priority_score=entry.priority_score,
                next_check_at=entry.next_check_at,
                last_metrics_at=entry.last_metrics_at,
                attempt_count=entry.attempt_count,
                source_scan_run_id=(
                    str(entry.source_scan_run_id) if entry.source_scan_run_id is not None else None
                ),
            )
        )
    return items


def _resolve_fast_stop_state(*, risk_band: RiskBand, ad, action_job) -> FastStopState:
    if action_job is not None:
        if action_job.status == ActionJobStatus.RUNNING:
            return FastStopState.RUNNING
        if action_job.status in {ActionJobStatus.QUEUED, ActionJobStatus.RETRYING}:
            return FastStopState.QUEUED
        if action_job.status == ActionJobStatus.FAILED:
            return FastStopState.FAILED
    if ad is not None and getattr(ad, "delivery_status", None) == DeliveryStatus.PAUSED:
        return FastStopState.PAUSED
    if risk_band == RiskBand.STOP:
        return FastStopState.STOP
    if risk_band == RiskBand.WATCH:
        return FastStopState.WATCH
    return FastStopState.IDLE


async def _resolve_profile_value(
    *, browser_repo: BrowserRepository, cache: dict[str, str], profile_id: str | None
) -> str | None:
    if profile_id is None:
        return None
    cached = cache.get(profile_id)
    if cached is not None:
        return cached
    profile = await browser_repo.get_profile(profile_id)
    if profile is None:
        return None
    cache[profile_id] = profile.vendor_profile_id
    return profile.vendor_profile_id


async def _resolve_browser_host_value(
    *,
    browser_repo: BrowserRepository,
    cache: dict[str, str],
    browser_host_id: str | None,
) -> str | None:
    if browser_host_id is None:
        return None
    cached = cache.get(browser_host_id)
    if cached is not None:
        return cached
    browser_host = await browser_repo.get_browser_host(browser_host_id)
    if browser_host is None:
        return None
    cache[browser_host_id] = browser_host.name
    return browser_host.name
