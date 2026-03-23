from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BotModeResponse(BaseModel):
    """Ответ с текущими настройками режима бота."""

    model_config = ConfigDict(from_attributes=True)

    auto_pause_enabled: bool = Field(description="Включено ли автоматическое паузирование")
    auto_resume_enabled: bool = Field(description="Включено ли автоматическое возобновление")
    observe_only_enabled: bool = Field(
        description="Включен ли режим наблюдения без реальных действий"
    )
    updated_at: datetime = Field(description="Время последнего обновления")


class BotModeUpdateRequest(BaseModel):
    """Запрос на обновление режима бота."""

    auto_pause_enabled: bool = Field(description="Включить автоматическое паузирование")
    auto_resume_enabled: bool = Field(description="Включить автоматическое возобновление")
    observe_only_enabled: bool = Field(
        description="Включить режим наблюдения без реальных действий"
    )


class ServiceSettingsResponse(BaseModel):
    """Ответ с полным набором runtime-настроек сервиса."""

    model_config = ConfigDict(from_attributes=True)

    auto_pause_enabled: bool
    auto_resume_enabled: bool
    auto_resume_available: bool
    observe_only_enabled: bool
    full_scan_interval_seconds: int
    recheck_interval_seconds: int
    full_scan_profile_concurrency: int
    action_worker_concurrency: int
    vision_local_api_url: str
    vision_cloud_api_url: str
    telegram_chat_id: str
    vision_api_token_masked: str | None = None
    telegram_bot_token_masked: str | None = None
    vision_api_token_configured: bool
    telegram_bot_token_configured: bool
    updated_at: datetime


class ServiceSettingsUpdateRequest(BaseModel):
    """Запрос на обновление runtime-настроек сервиса."""

    auto_pause_enabled: bool
    auto_resume_enabled: bool
    observe_only_enabled: bool
    full_scan_interval_seconds: int = Field(description="Частота полного цикла сканирования")
    recheck_interval_seconds: int = Field(description="Частота быстрой перепроверки")
    full_scan_profile_concurrency: int = Field(
        description="Параллельность полного скана по профилям"
    )
    action_worker_concurrency: int = Field(
        description="Параллельность очереди действий по профилям"
    )
    vision_local_api_url: str
    vision_cloud_api_url: str
    telegram_chat_id: str = ""
    vision_api_token: str | None = None
    telegram_bot_token: str | None = None


class SuspendedProfileItem(BaseModel):
    """Профиль, для которого сканирование остановлено."""

    profile_id: str
    display_name: str
    browser_host_id: str
    reason: str
    suspended_at: datetime


class SuspendedProfileResetResponse(BaseModel):
    """Ответ после ручного снятия стопа с профиля."""

    message: str
    profile: SuspendedProfileItem
