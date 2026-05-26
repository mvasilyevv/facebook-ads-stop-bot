# -*- coding: utf-8 -*-
"""Базовые типы для пакета tools/.

Определяет протокол ToolHandler, ToolError и RiskLevel.
Не содержит бизнес-логики конкретных tools.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """Уровень риска tool'а — определяет, исполняется он сразу или требует подтверждения."""

    READ_ONLY = "read_only"
    """Безопасно, исполняется сразу."""

    DRAFT_REQUIRED = "draft_required"
    """Создаёт *MutationTask со status=DRAFT, юзер подтверждает в TG."""

    CREATIVE = "creative"
    """LLM-генерация, не вызывает Meta API напрямую."""


class ToolError(Exception):
    """Контролируемая ошибка tool'а — не падает в Anthropic SDK, отдаётся LLM как ошибка."""


@runtime_checkable
class ToolHandler(Protocol):
    """Протокол для tool-обработчика.

    Каждый tool — объект с полями name, schema, risk_level и методом run().
    """

    name: str
    """Уникальное имя tool'а (используется в whitelist и JSON Schema)."""

    schema: dict[str, Any]
    """JSON Schema для tool'а (формат Anthropic tool-use)."""

    risk_level: RiskLevel
    """Уровень риска."""

    async def run(self, args: dict[str, Any]) -> str:
        """Исполнить tool. Возвращает текстовый результат для LLM.

        Для DRAFT_REQUIRED tools — возвращает task_id + summary, фактическое
        исполнение происходит после подтверждения юзером.
        """
        ...
