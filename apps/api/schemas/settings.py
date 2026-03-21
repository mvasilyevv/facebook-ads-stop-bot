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
