from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import DbSessionDep
from apps.api.schemas.profiles import ProfileItem
from core.repositories import BrowserRepository

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileItem])
async def list_profiles(session: DbSessionDep) -> list[ProfileItem]:
    repo = BrowserRepository(session)
    profiles = await repo.list_profiles()
    return [
        ProfileItem(
            profile_id=item.profile.vendor_profile_id,
            display_name=item.profile.display_name,
            browser_host_id=item.browser_host.name,
            is_active=item.profile.is_active,
            scan_suspended=item.profile.scan_suspended,
            last_launch_at=item.profile.last_launch_at,
        )
        for item in profiles
    ]
