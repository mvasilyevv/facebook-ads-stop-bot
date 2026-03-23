from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.action_jobs import ActionJobItem
from core.repositories import ActionJobsRepository, AdsRepository, BrowserRepository

router = APIRouter(prefix="/action-jobs", tags=["action-jobs"])


@router.get("", response_model=list[ActionJobItem], status_code=status.HTTP_200_OK)
async def list_action_jobs(
    session: DbSessionDep,
    profile_id: str | None = Query(default=None),
) -> list[ActionJobItem]:
    resolved_profile_id = None
    if profile_id is not None:
        profile = await BrowserRepository(session).get_profile_by_vendor_id(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Профиль `{profile_id}` не найден",
            )
        resolved_profile_id = str(profile.id)

    repo = ActionJobsRepository(session)
    jobs = await repo.list_jobs(limit=200)
    if resolved_profile_id is not None:
        jobs = [job for job in jobs if str(job.profile_id) == resolved_profile_id]

    fb_ad_ids = [job.fb_ad_id for job in jobs]
    ads = await AdsRepository(session).get_ads_by_fb_ad_ids(fb_ad_ids)
    browser_repo = BrowserRepository(session)
    profile_cache: dict[str, str] = {}
    browser_host_cache: dict[str, str] = {}

    items: list[ActionJobItem] = []
    for job in jobs:
        ad = ads.get(job.fb_ad_id)
        items.append(
            ActionJobItem(
                id=str(job.id),
                fb_ad_id=job.fb_ad_id,
                profile_id=await _resolve_profile_value(
                    browser_repo=browser_repo,
                    cache=profile_cache,
                    profile_id=str(job.profile_id) if job.profile_id is not None else None,
                ),
                browser_host_id=await _resolve_browser_host_value(
                    browser_repo=browser_repo,
                    cache=browser_host_cache,
                    browser_host_id=(
                        str(job.browser_host_id) if job.browser_host_id is not None else None
                    ),
                ),
                campaign_name=getattr(getattr(ad, "campaign", None), "name", None),
                adset_name=getattr(getattr(ad, "adset", None), "name", None),
                ad_name=getattr(ad, "name", None),
                action_type=job.action_type.value,
                status=job.status.value,
                priority_score=job.priority_score,
                attempt_count=job.attempt_count,
                next_attempt_at=job.next_attempt_at,
                last_error=job.last_error,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
        )
    return items


async def _resolve_profile_value(
    *,
    browser_repo: BrowserRepository,
    cache: dict[str, str],
    profile_id: str | None,
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
