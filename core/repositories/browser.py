from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.browser import BrowserHost, BrowserSession, Profile
from core.repositories.base import AsyncRepository


@dataclass(slots=True, frozen=True)
class BrowserSessionRecord:
    """Нормализованное представление browser session вместе с профилем и хостом."""

    session: BrowserSession
    profile: Profile
    browser_host: BrowserHost


@dataclass(slots=True, frozen=True)
class ActiveProfileRecord:
    """Нормализованное представление активного профиля вместе с browser host."""

    profile: Profile
    browser_host: BrowserHost


class BrowserRepository(AsyncRepository):
    """Репозиторий browser host, профилей и последних сессий."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_browser_host_by_name(self, name: str) -> BrowserHost | None:
        result = await self.session.scalars(select(BrowserHost).where(BrowserHost.name == name))
        return result.first()

    async def upsert_browser_host(
        self,
        *,
        name: str,
        vendor: str,
        api_base_url: str,
        is_enabled: bool = True,
        last_heartbeat_at: datetime | None = None,
    ) -> BrowserHost:
        stmt = (
            pg_insert(BrowserHost)
            .values(
                name=name,
                vendor=vendor,
                api_base_url=api_base_url,
                is_enabled=is_enabled,
                last_heartbeat_at=last_heartbeat_at,
            )
            .on_conflict_do_update(
                index_elements=[BrowserHost.name],
                set_={
                    "vendor": vendor,
                    "api_base_url": api_base_url,
                    "is_enabled": is_enabled,
                    "last_heartbeat_at": last_heartbeat_at,
                },
            )
            .returning(BrowserHost)
        )
        result = await self.session.execute(stmt)
        browser_host = result.scalars().one()
        await self.session.flush()
        return browser_host

    async def get_profile_by_vendor_id(self, vendor_profile_id: str) -> Profile | None:
        result = await self.session.scalars(
            select(Profile).where(Profile.vendor_profile_id == vendor_profile_id)
        )
        return result.first()

    async def upsert_profile(
        self,
        *,
        browser_host_id: str,
        vendor_profile_id: str,
        display_name: str,
        is_active: bool,
        last_launch_at: datetime | None = None,
    ) -> Profile:
        profile = await self.get_profile_by_vendor_id(vendor_profile_id)
        if profile is None:
            profile = Profile(
                browser_host_id=browser_host_id,
                vendor_profile_id=vendor_profile_id,
                display_name=display_name,
                is_active=is_active,
                last_launch_at=last_launch_at,
            )
            self.session.add(profile)
        else:
            profile.browser_host_id = browser_host_id
            profile.display_name = display_name
            profile.is_active = is_active
            profile.last_launch_at = last_launch_at
        await self.session.flush()
        return profile

    async def create_browser_session(
        self,
        *,
        browser_host_id: str,
        profile_id: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        cdp_url: str | None = None,
        webdriver_url: str | None = None,
        error_message: str | None = None,
    ) -> BrowserSession:
        browser_session = BrowserSession(
            browser_host_id=browser_host_id,
            profile_id=profile_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            cdp_url=cdp_url,
            webdriver_url=webdriver_url,
            error_message=error_message,
        )
        self.session.add(browser_session)
        await self.session.flush()
        return browser_session

    async def list_latest_sessions(self) -> list[BrowserSessionRecord]:
        stmt = (
            select(BrowserSession, Profile, BrowserHost)
            .join(Profile, BrowserSession.profile_id == Profile.id)
            .join(BrowserHost, BrowserSession.browser_host_id == BrowserHost.id)
            .order_by(Profile.vendor_profile_id, BrowserSession.started_at.desc())
        )
        result = await self.session.execute(stmt)
        latest_sessions: list[BrowserSessionRecord] = []
        seen_profile_ids: set[str] = set()
        for browser_session, profile, browser_host in result.all():
            if profile.id in seen_profile_ids:
                continue
            seen_profile_ids.add(profile.id)
            latest_sessions.append(
                BrowserSessionRecord(
                    session=browser_session,
                    profile=profile,
                    browser_host=browser_host,
                )
            )
        return latest_sessions

    async def get_latest_session_by_vendor_profile_id(
        self, vendor_profile_id: str
    ) -> BrowserSessionRecord | None:
        stmt = (
            select(BrowserSession, Profile, BrowserHost)
            .join(Profile, BrowserSession.profile_id == Profile.id)
            .join(BrowserHost, BrowserSession.browser_host_id == BrowserHost.id)
            .where(Profile.vendor_profile_id == vendor_profile_id)
            .order_by(BrowserSession.started_at.desc())
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        browser_session, profile, browser_host = row
        return BrowserSessionRecord(
            session=browser_session,
            profile=profile,
            browser_host=browser_host,
        )

    async def list_active_profiles(self) -> list[ActiveProfileRecord]:
        stmt = (
            select(Profile, BrowserHost)
            .join(BrowserHost, Profile.browser_host_id == BrowserHost.id)
            .where(Profile.is_active.is_(True), BrowserHost.is_enabled.is_(True))
            .order_by(BrowserHost.name, Profile.vendor_profile_id)
        )
        result = await self.session.execute(stmt)
        return [
            ActiveProfileRecord(profile=profile, browser_host=browser_host)
            for profile, browser_host in result.all()
        ]
