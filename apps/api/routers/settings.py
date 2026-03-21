from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.settings import BotModeResponse, BotModeUpdateRequest
from core.config import get_settings
from core.repositories.operations import SystemSettingsRepository

router = APIRouter(prefix="/settings", tags=["settings"])

# Ключи для системных настроек
SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"


def _parse_bool_setting(value: str) -> bool:
    """Преобразовать строковое значение в логическое."""
    return value.lower() in ("true", "1", "yes", "on")


def _bool_to_string(value: bool) -> str:
    """Преобразовать логическое значение в строку."""
    return "true" if value else "false"


@router.get("/bot-mode", response_model=BotModeResponse, status_code=status.HTTP_200_OK)
async def get_bot_mode(session: DbSessionDep) -> BotModeResponse:
    """Получить текущие настройки режима бота."""
    repo = SystemSettingsRepository(session)
    settings = await repo.get_all_settings()
    default_settings = get_settings()

    # Получить значения из БД или использовать значения по умолчанию из конфига
    auto_pause_enabled = _parse_bool_setting(
        settings.get(SETTING_AUTO_PAUSE_ENABLED, str(default_settings.feature_auto_pause).lower())
    )
    auto_resume_enabled = _parse_bool_setting(
        settings.get(SETTING_AUTO_RESUME_ENABLED, str(default_settings.feature_auto_resume).lower())
    )
    observe_only_enabled = _parse_bool_setting(
        settings.get(
            SETTING_OBSERVE_ONLY_ENABLED, str(default_settings.feature_observe_only).lower()
        )
    )

    # Получить время последнего обновления или текущее время
    auto_pause_setting = await repo.get_setting(SETTING_AUTO_PAUSE_ENABLED)
    auto_resume_setting = await repo.get_setting(SETTING_AUTO_RESUME_ENABLED)
    observe_only_setting = await repo.get_setting(SETTING_OBSERVE_ONLY_ENABLED)

    # Используем время обновления из БД, если есть, иначе текущее время
    existing_settings = [
        item.updated_at
        for item in (auto_pause_setting, auto_resume_setting, observe_only_setting)
        if item is not None
    ]
    if existing_settings:
        updated_at = max(existing_settings)
    else:
        updated_at = datetime.now(tz=UTC)

    return BotModeResponse(
        auto_pause_enabled=auto_pause_enabled,
        auto_resume_enabled=auto_resume_enabled,
        observe_only_enabled=observe_only_enabled,
        updated_at=updated_at,
    )


@router.put("/bot-mode", response_model=BotModeResponse, status_code=status.HTTP_200_OK)
async def update_bot_mode(
    payload: BotModeUpdateRequest,
    session: DbSessionDep,
) -> BotModeResponse:
    """Обновить настройки режима бота."""
    repo = SystemSettingsRepository(session)

    # Сохранить обновленные значения в БД
    await repo.set_setting(
        SETTING_AUTO_PAUSE_ENABLED,
        _bool_to_string(payload.auto_pause_enabled),
        description="Автоматическое паузирование объявлений",
    )
    await repo.set_setting(
        SETTING_AUTO_RESUME_ENABLED,
        _bool_to_string(payload.auto_resume_enabled),
        description="Автоматическое возобновление объявлений",
    )
    await repo.set_setting(
        SETTING_OBSERVE_ONLY_ENABLED,
        _bool_to_string(payload.observe_only_enabled),
        description="Режим наблюдения без реальных действий",
    )

    await session.commit()

    # Получить обновленные значения и вернуть
    return await get_bot_mode(session)
