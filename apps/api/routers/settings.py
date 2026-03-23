from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.settings import (
    BotModeResponse,
    BotModeUpdateRequest,
    ServiceSettingsResponse,
    ServiceSettingsUpdateRequest,
    SuspendedProfileItem,
    SuspendedProfileResetResponse,
)
from core.config import get_settings
from core.repositories import BrowserRepository
from core.repositories.operations import SystemSettingsRepository
from core.services import (
    SERVICE_SETTING_AUTO_PAUSE_ENABLED,
    SERVICE_SETTING_AUTO_RESUME_ENABLED,
    SERVICE_SETTING_OBSERVE_ONLY_ENABLED,
    SERVICE_SETTING_SCAN_INTERVAL_SECONDS,
    SERVICE_SETTING_TELEGRAM_BOT_TOKEN,
    SERVICE_SETTING_TELEGRAM_CHAT_ID,
    SERVICE_SETTING_VISION_API_TOKEN,
    SERVICE_SETTING_VISION_CLOUD_API_URL,
    SERVICE_SETTING_VISION_LOCAL_API_URL,
    mask_secret,
    resolve_service_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _parse_bool_setting(value: str) -> bool:
    """Преобразовать строковое значение в логическое."""
    return value.lower() in ("true", "1", "yes", "on")


def _bool_to_string(value: bool) -> str:
    """Преобразовать логическое значение в строку."""
    return "true" if value else "false"


@router.get("/bot-mode", response_model=BotModeResponse, status_code=status.HTTP_200_OK)
async def get_bot_mode(session: DbSessionDep) -> BotModeResponse:
    """Получить текущие настройки режима бота."""
    runtime = await resolve_service_settings(session, base_settings=get_settings())

    return BotModeResponse(
        auto_pause_enabled=runtime.auto_pause_enabled,
        auto_resume_enabled=runtime.auto_resume_enabled,
        observe_only_enabled=runtime.observe_only_enabled,
        updated_at=runtime.updated_at,
    )


@router.put("/bot-mode", response_model=BotModeResponse, status_code=status.HTTP_200_OK)
async def update_bot_mode(
    payload: BotModeUpdateRequest,
    session: DbSessionDep,
) -> BotModeResponse:
    """Обновить настройки режима бота."""
    repo = SystemSettingsRepository(session)
    default_settings = get_settings()

    await repo.set_setting(
        SERVICE_SETTING_AUTO_PAUSE_ENABLED,
        _bool_to_string(payload.auto_pause_enabled),
        description="Автоматическое паузирование объявлений",
    )
    await repo.set_setting(
        SERVICE_SETTING_AUTO_RESUME_ENABLED,
        _bool_to_string(
            payload.auto_resume_enabled if default_settings.feature_auto_resume else False
        ),
        description="Автоматическое возобновление объявлений",
    )
    await repo.set_setting(
        SERVICE_SETTING_OBSERVE_ONLY_ENABLED,
        _bool_to_string(payload.observe_only_enabled),
        description="Режим наблюдения без реальных действий",
    )

    await session.commit()
    return await get_bot_mode(session)


def _map_suspended_profile(item) -> SuspendedProfileItem:
    return SuspendedProfileItem(
        profile_id=item.profile.vendor_profile_id,
        display_name=item.profile.display_name,
        browser_host_id=item.browser_host.name,
        reason=item.profile.scan_suspend_reason or "Сканирование остановлено",
        suspended_at=item.profile.scan_suspend_at or datetime.now(tz=UTC),
    )


@router.get("/service", response_model=ServiceSettingsResponse, status_code=status.HTTP_200_OK)
async def get_service_settings(session: DbSessionDep) -> ServiceSettingsResponse:
    """Получить полный набор runtime-настроек сервиса."""

    runtime = await resolve_service_settings(session, base_settings=get_settings())
    return ServiceSettingsResponse(
        auto_pause_enabled=runtime.auto_pause_enabled,
        auto_resume_enabled=runtime.auto_resume_enabled,
        auto_resume_available=runtime.auto_resume_available,
        observe_only_enabled=runtime.observe_only_enabled,
        scan_interval_seconds=runtime.scan_interval_seconds,
        vision_local_api_url=runtime.vision_local_api_url,
        vision_cloud_api_url=runtime.vision_cloud_api_url,
        telegram_chat_id=runtime.telegram_chat_id,
        vision_api_token_masked=mask_secret(runtime.vision_api_token),
        telegram_bot_token_masked=mask_secret(runtime.telegram_bot_token),
        vision_api_token_configured=bool(runtime.vision_api_token),
        telegram_bot_token_configured=bool(runtime.telegram_bot_token),
        updated_at=runtime.updated_at,
    )


@router.put("/service", response_model=ServiceSettingsResponse, status_code=status.HTTP_200_OK)
async def update_service_settings(
    payload: ServiceSettingsUpdateRequest,
    session: DbSessionDep,
) -> ServiceSettingsResponse:
    """Обновить runtime-настройки сервиса."""

    if payload.scan_interval_seconds not in {30, 60, 120, 300}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Допустимая частота скана: 30, 60, 120 или 300 секунд",
        )

    repo = SystemSettingsRepository(session)
    default_settings = get_settings()
    await repo.set_setting(
        SERVICE_SETTING_AUTO_PAUSE_ENABLED,
        _bool_to_string(payload.auto_pause_enabled),
        description="Автоматическое паузирование объявлений",
    )
    await repo.set_setting(
        SERVICE_SETTING_AUTO_RESUME_ENABLED,
        _bool_to_string(
            payload.auto_resume_enabled if default_settings.feature_auto_resume else False
        ),
        description="Автоматическое возобновление объявлений",
    )
    await repo.set_setting(
        SERVICE_SETTING_OBSERVE_ONLY_ENABLED,
        _bool_to_string(payload.observe_only_enabled),
        description="Режим наблюдения без реальных действий",
    )
    await repo.set_setting(
        SERVICE_SETTING_SCAN_INTERVAL_SECONDS,
        str(payload.scan_interval_seconds),
        description="Частота полного цикла сканирования",
    )
    await repo.set_setting(
        SERVICE_SETTING_VISION_LOCAL_API_URL,
        payload.vision_local_api_url.strip(),
        description="Локальный URL Vision API",
    )
    await repo.set_setting(
        SERVICE_SETTING_VISION_CLOUD_API_URL,
        payload.vision_cloud_api_url.strip(),
        description="Облачный URL Vision API",
    )
    await repo.set_setting(
        SERVICE_SETTING_TELEGRAM_CHAT_ID,
        payload.telegram_chat_id.strip(),
        description="Идентификатор чата Telegram",
    )
    if payload.vision_api_token is not None:
        await repo.set_setting(
            SERVICE_SETTING_VISION_API_TOKEN,
            payload.vision_api_token.strip(),
            description="Токен Vision API",
        )
    if payload.telegram_bot_token is not None:
        await repo.set_setting(
            SERVICE_SETTING_TELEGRAM_BOT_TOKEN,
            payload.telegram_bot_token.strip(),
            description="Токен Telegram-бота",
        )
    await session.commit()
    return await get_service_settings(session)


@router.get(
    "/suspended-profiles",
    response_model=list[SuspendedProfileItem],
    status_code=status.HTTP_200_OK,
)
async def list_suspended_profiles(session: DbSessionDep) -> list[SuspendedProfileItem]:
    """Вернуть профили, для которых сканирование остановлено."""

    repo = BrowserRepository(session)
    suspended_profiles = await repo.list_suspended_profiles()
    return [_map_suspended_profile(item) for item in suspended_profiles]


@router.post(
    "/suspended-profiles/{profile_id}/reset",
    response_model=SuspendedProfileResetResponse,
    status_code=status.HTTP_200_OK,
)
async def reset_suspended_profile(
    profile_id: str,
    session: DbSessionDep,
) -> SuspendedProfileResetResponse:
    """Снять ручной стоп сканирования с профиля."""

    repo = BrowserRepository(session)
    profile = await repo.reset_profile_scan_suspension(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Профиль `{profile_id}` не найден",
        )
    await session.commit()
    refreshed = await repo.get_profile_by_vendor_id(profile_id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Профиль `{profile_id}` не найден после сброса стопа",
        )
    browser_host = await repo.get_browser_host(refreshed.browser_host_id)
    if browser_host is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Browser host для профиля `{profile_id}` не найден",
        )
    return SuspendedProfileResetResponse(
        message="Сканирование профиля снова разрешено",
        profile=SuspendedProfileItem(
            profile_id=refreshed.vendor_profile_id,
            display_name=refreshed.display_name,
            browser_host_id=browser_host.name,
            reason="Сканирование разрешено вручную",
            suspended_at=datetime.now(tz=UTC),
        ),
    )
