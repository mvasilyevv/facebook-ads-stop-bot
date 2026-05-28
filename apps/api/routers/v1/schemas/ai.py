# -*- coding: utf-8 -*-
"""Pydantic-схемы для AI-анализа (POST /ai/analyze)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Поддерживаемые типы блоков анализа
AnalysisBlockType = Literal[
    "dashboard_overview",
    "ad_detail",
    "campaign_summary",
    "history_summary",
]


class AIAnalyzeRequest(BaseModel):
    """Тело запроса AI-анализа."""

    block_type: AnalysisBlockType
    scope_key: str = Field(default="global", description="'global' или конкретный ID")
    force_refresh: bool = Field(default=False, description="Игнорировать Redis-кэш")
    client_data: dict[str, Any] | None = Field(
        default=None,
        description="Контекстные данные от клиента (опционально)",
    )


class AIAnalyzeResponse(BaseModel):
    """Ответ AI-анализа."""

    model_config = ConfigDict(from_attributes=False)

    block_type: str
    scope_key: str
    analysis_text: str
    from_cache: bool
    generated_at: str
    model: str
