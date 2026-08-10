# -*- coding: utf-8 -*-
"""Базовые типы пакета core.ai_assistant.tools.

Содержит:
- RiskLevel — категория tool'а (READ_ONLY / CREATIVE).
- ToolError — контролируемая ошибка tool'а, отдаётся LLM как tool_result error.
- ToolContext — DI-контейнер с зависимостями, передаётся в `run()`.
- ToolHandler — Protocol для конкретных tool-классов.

Бизнес-логика конкретных tools здесь не лежит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from sqlalchemy.ext.asyncio import AsyncEngine

    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """Категория tool'а — определяет правила исполнения."""

    READ_ONLY = "read_only"
    """Чтение, исполняется немедленно (БД / Redis / Meta API READ)."""

    CREATIVE = "creative"
    """Генерация контента через LLM, без mutations."""


class ToolError(Exception):
    """Контролируемая ошибка tool'а — LLM получит её как tool_result.is_error=true."""


@dataclass(slots=True, frozen=True)
class ToolContext:
    """DI-контейнер для tool'ов.

    Создаётся ChatSession и пробрасывается в каждый вызов `ToolHandler.run`.

    Поля nullable, чтобы tool, которому нужен только client_key (например creative),
    мог работать без engine/redis. Tools должны валидировать что нужное поле
    задано — иначе бросать ToolError("требуется engine/redis/meta_api_client").
    """

    client_key: str
    """Идентификатор клиента/инициатора (rate-limit ключ + audit)."""

    engine: AsyncEngine | None = None
    """SQLAlchemy AsyncEngine для read-only БД-запросов."""

    redis_client: Any | None = None
    """redis.asyncio.Redis или совместимый — только для disposable AI rate limits/cache."""

    meta_api_client: MetaApiClient | None = None
    """Опциональный — нужен только meta/* tools."""

    requested_by: str = ""
    """Optional caller label retained for read-tool audit context."""

    def require_engine(self) -> AsyncEngine:
        """Вернуть engine или поднять ToolError."""
        if self.engine is None:
            raise ToolError("ToolContext.engine не задан — БД-операции недоступны")
        return self.engine

    def require_meta_api(self) -> MetaApiClient:
        """Вернуть meta_api_client или поднять ToolError."""
        if self.meta_api_client is None:
            raise ToolError(
                "ToolContext.meta_api_client не задан — Marketing API недоступен в этой сессии"
            )
        return self.meta_api_client

    def require_redis(self) -> Any:
        """Вернуть redis_client или поднять ToolError."""
        if self.redis_client is None:
            raise ToolError("ToolContext.redis_client не задан")
        return self.redis_client

    def effective_requested_by(self) -> str:
        """requested_by если задан, иначе формирует `ai:{client_key}`."""
        return self.requested_by or f"ai:{self.client_key}"


@runtime_checkable
class ToolHandler(Protocol):
    """Протокол tool-обработчика. Реализующий класс — stateless, регистрируется один раз."""

    name: str
    """Уникальное имя tool'а — должно совпадать со `name` в JSON Schema."""

    schema: dict[str, Any]
    """JSON Schema (формат Anthropic tool-use) — отдаётся LLM в tools=."""

    risk_level: RiskLevel
    """Категория tool'а."""

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        """Исполнить tool. Возвращает текстовый результат для LLM.

        Должен поднимать ToolError на ожидаемых ошибках (валидация args,
        недоступность зависимостей). Непредвиденные исключения — пусть падают,
        registry их перехватит и завернёт в ToolError.
        """
        ...
