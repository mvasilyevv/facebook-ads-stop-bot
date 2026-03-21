from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.sessions import (
    BrowserSessionItem,
    SessionActionResponse,
    SessionControlRequest,
)
from apps.browser_host.adapters import AdapterConnectionError, AdapterProtocolError
from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from core.config import get_settings
from core.repositories import BrowserRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def build_session_manager(settings) -> BrowserSessionManager:
    """Собирает browser session manager для runtime и тестовых подмен."""

    return BrowserSessionManager(
        adapter=build_adapter(settings),
        playwright_attach_service=PlaywrightAttachService(),
    )


def _map_session_item(record) -> BrowserSessionItem:
    if record.session.error_message:
        last_message = record.session.error_message
    elif record.session.status == "ACTIVE":
        last_message = "Сессия активна"
    elif record.session.status == "STOPPED":
        last_message = "Сессия остановлена"
    else:
        last_message = "Состояние сессии обновлено"
    return BrowserSessionItem(
        profile_id=record.profile.vendor_profile_id,
        browser_host_id=record.browser_host.name,
        status=record.session.status,
        cdp_url=record.session.cdp_url,
        webdriver_url=record.session.webdriver_url,
        last_started_at=record.session.started_at,
        last_stopped_at=record.session.finished_at,
        last_message=last_message,
    )


async def _sync_vision_profiles(repo: BrowserRepository) -> None:
    """Синхронизирует открытые профили Vision с базой данных.

    Запрашивает у Vision локального API текущие открытые профили
    и для каждого создаёт/обновляет записи browser_host, profile и session.
    Профили, которые были ACTIVE в базе, но уже закрыты в Vision, помечаются как STOPPED.
    """
    settings = get_settings()
    adapter = build_adapter(settings)
    try:
        open_profiles = await adapter.list_open_profiles()
    except (AdapterConnectionError, AdapterProtocolError) as exc:
        logger.warning("Не удалось получить открытые профили из Vision: %s", exc)
        return

    open_profile_ids: set[str] = set()
    now = datetime.now(tz=UTC)
    host_name = f"vision-{settings.vision_local_api_url.split(':', maxsplit=3)[-1]}"

    for open_profile in open_profiles:
        open_profile_ids.add(open_profile.profile_id)
        browser_host = await repo.upsert_browser_host(
            name=host_name,
            vendor=settings.browser_vendor,
            api_base_url=settings.vision_local_api_url,
            is_enabled=True,
            last_heartbeat_at=now,
        )
        profile = await repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id=open_profile.profile_id,
            display_name=open_profile.display_name,
            is_active=True,
            last_launch_at=now,
        )
        existing = await repo.get_latest_session_by_vendor_profile_id(open_profile.profile_id)
        if existing is None or existing.session.status != "ACTIVE":
            cdp_url = open_profile.debug_endpoint
            await repo.create_browser_session(
                browser_host_id=browser_host.id,
                profile_id=profile.id,
                status="ACTIVE",
                started_at=now,
                cdp_url=cdp_url,
            )

    db_active = await repo.list_active_profiles()
    for record in db_active:
        if record.profile.vendor_profile_id not in open_profile_ids:
            record.profile.is_active = False
            latest = await repo.get_latest_session_by_vendor_profile_id(
                record.profile.vendor_profile_id
            )
            if latest is not None and latest.session.status == "ACTIVE":
                latest.session.status = "STOPPED"
                latest.session.finished_at = now

    await repo.session.flush()


@router.get("", response_model=list[BrowserSessionItem])
async def list_sessions(session: DbSessionDep) -> list[BrowserSessionItem]:
    repo = BrowserRepository(session)
    await _sync_vision_profiles(repo)
    await session.commit()
    sessions = await repo.list_latest_sessions()
    return [_map_session_item(item) for item in sessions]


@router.get("/{profile_id}", response_model=BrowserSessionItem)
async def get_session(profile_id: str, session: DbSessionDep) -> BrowserSessionItem:
    repo = BrowserRepository(session)
    browser_session = await repo.get_latest_session_by_vendor_profile_id(profile_id)
    if browser_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена")
    return _map_session_item(browser_session)


@router.post("/{profile_id}/start", response_model=SessionActionResponse)
async def start_session(
    profile_id: str, payload: SessionControlRequest, session: DbSessionDep
) -> SessionActionResponse:
    settings = get_settings()
    manager = build_session_manager(settings)
    repo = BrowserRepository(session)
    attached_session = None
    try:
        attached_session = await manager.ensure_session(profile_id)
    except (AdapterConnectionError, AdapterProtocolError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Не удалось запустить browser session: {exc}",
        ) from exc
    try:
        browser_host = await repo.upsert_browser_host(
            name=payload.browser_host_id,
            vendor=settings.browser_vendor,
            api_base_url=settings.vision_local_api_url,
            is_enabled=True,
            last_heartbeat_at=datetime.now(tz=UTC),
        )
        profile = await repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id=profile_id,
            display_name=profile_id,
            is_active=True,
            last_launch_at=datetime.now(tz=UTC),
        )
        await repo.create_browser_session(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status="ACTIVE",
            started_at=datetime.now(tz=UTC),
            cdp_url=attached_session.cdp_url,
            webdriver_url=attached_session.webdriver_url,
        )
        await session.commit()
        record = await repo.get_latest_session_by_vendor_profile_id(profile_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Сессия не сохранена"
            )
        return SessionActionResponse(
            message="Сессия успешно запущена",
            session=_map_session_item(record),
        )
    finally:
        if attached_session is not None:
            await manager.release_session(attached_session)


@router.post("/{profile_id}/stop", response_model=SessionActionResponse)
async def stop_session(
    profile_id: str, payload: SessionControlRequest, session: DbSessionDep
) -> SessionActionResponse:
    settings = get_settings()
    adapter = build_adapter(settings)
    repo = BrowserRepository(session)
    try:
        await adapter.stop_profile(profile_id)
    except (AdapterConnectionError, AdapterProtocolError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Не удалось остановить browser session: {exc}",
        ) from exc

    browser_host = await repo.upsert_browser_host(
        name=payload.browser_host_id,
        vendor=settings.browser_vendor,
        api_base_url=settings.vision_local_api_url,
        is_enabled=True,
        last_heartbeat_at=datetime.now(tz=UTC),
    )
    profile = await repo.upsert_profile(
        browser_host_id=browser_host.id,
        vendor_profile_id=profile_id,
        display_name=profile_id,
        is_active=False,
        last_launch_at=None,
    )
    latest_record = await repo.get_latest_session_by_vendor_profile_id(profile_id)
    finished_at = datetime.now(tz=UTC)
    if latest_record is None:
        await repo.create_browser_session(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            status="STOPPED",
            started_at=finished_at,
            finished_at=finished_at,
        )
    else:
        latest_record.session.status = "STOPPED"
        latest_record.session.finished_at = finished_at
    await session.commit()

    updated_record = await repo.get_latest_session_by_vendor_profile_id(profile_id)
    if updated_record is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Сессия не сохранена"
        )
    return SessionActionResponse(
        message="Сессия успешно остановлена",
        session=_map_session_item(updated_record),
    )
