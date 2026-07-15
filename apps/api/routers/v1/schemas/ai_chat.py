# -*- coding: utf-8 -*-
"""Схемы веб-чата с ассистентом (POST /ai/chat)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """Одно сообщение истории (историю держит клиент и шлёт с каждым запросом)."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AIChatRequest(BaseModel):
    """Запрос чата: история (последнее сообщение — вопрос пользователя)."""

    messages: list[ChatMessageIn] = Field(min_length=1, max_length=12)


class ToolCallOut(BaseModel):
    """След вызова инструмента — фронт показывает «что ассистент проверял»."""

    name: str
    error: str | None = None


class AIChatResponse(BaseModel):
    """Ответ ассистента."""

    answer: str
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    generated_at: str
    model: str


class AIPulseResponse(BaseModel):
    """Почасовой пульс для веб-виджета.

    important=False → за окно ничего значимого, виджет молчит (text = null).
    """

    important: bool
    text: str | None = None
    generated_at: str
