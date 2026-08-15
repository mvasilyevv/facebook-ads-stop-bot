# -*- coding: utf-8 -*-
"""MCPContextManager — единый async-context lifecycle для MCP-сервера.

Создаёт ровно одну копию ресурсов, которые шарят все tool-вызовы и
read_resource хендлеры:

- SQLAlchemy AsyncEngine для read-only запросов.
- Redis client — disposable rate-limit (`ai:ratelimit:tools:mcp:claude-desktop`).
- MetaApiClient (опционально, lazy) — если browser-agent gRPC доступен,
  meta-tools читают Marketing API. При недоступности meta-tools отдают
  ToolError и LLM формирует ответ без них.

`build_tool_context()` возвращает `ToolContext` со стабильным
`client_key="mcp:claude-desktop"` — так все вызовы из Claude Desktop
попадают в один rate-limit bucket.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.ai_assistant.tools.base import ToolContext
from core.db import WORKER_ENGINE_KWARGS
from core.safe_diagnostics import safe_exception_diagnostic

if TYPE_CHECKING:  # pragma: no cover - только аннотации
    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)

# Стабильный client_key — все запросы из Claude Desktop попадают в один
# rate-limit ведро (30/час). Если пользователь параллельно сидит в TG /ask,
# у того будет свой client_key "tg:<user_id>" — лимиты независимы.
MCP_CLIENT_KEY = "mcp:claude-desktop"


def _resolve_database_url() -> str:
    """Получить database URL — env > .env > Settings."""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw:
        return _normalize_asyncpg(raw)
    from core.config import get_settings

    return _normalize_asyncpg(get_settings().database_url)


def _normalize_asyncpg(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _resolve_redis_url() -> str:
    raw = os.environ.get("REDIS_URL", "").strip()
    if raw:
        return raw
    try:
        from core.config import get_settings

        return get_settings().redis_url
    except Exception:
        return "redis://localhost:6380/0"


async def _build_meta_api_client() -> "MetaApiClient | None":
    """Build the optional Meta API client without coupling MCP startup to it.

    При недоступности browser-agent (gRPC) возвращаем None и пишем warning.
    meta-tools при `meta_api_client is None` поднимут ToolError с понятным
    текстом — LLM передаст это пользователю.
    """
    grpc_host = os.environ.get("BROWSER_AGENT_GRPC_HOST", "localhost")
    try:
        grpc_port = int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051"))
    except ValueError:
        grpc_port = 50051

    try:
        from core.meta_api.client import MetaApiClient

        client = MetaApiClient(host=grpc_host, port=grpc_port)
        await client.start()
        logger.info("MetaApiClient поднят (%s:%d) — meta-tools в MCP активны", grpc_host, grpc_port)
        return client
    except Exception as exc:
        logger.warning(
            "MetaApiClient не запустился — MCP продолжит работать без meta-tools (%s)",
            safe_exception_diagnostic(exc),
        )
        return None


@dataclass
class MCPContextManager:
    """Async-context, держит общие ресурсы MCP-сервера на время процесса.

    Поля nullable — в тестах удобно подменять mock'и через прямое поле
    без вызова __aenter__.
    """

    database_url: str | None = None
    redis_url: str | None = None
    enable_meta_api: bool = True

    engine: AsyncEngine | None = None
    redis_client: Any | None = None
    meta_api_client: "MetaApiClient | None" = None

    _entered: bool = field(default=False, init=False, repr=False)

    async def __aenter__(self) -> "MCPContextManager":
        if self._entered:
            return self
        db_url = self.database_url or _resolve_database_url()
        redis_url = self.redis_url or _resolve_redis_url()

        self.engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
        logger.info("MCP context: AsyncEngine инициализирован (%s)", _safe_dsn(db_url))

        try:
            from redis.asyncio import Redis  # type: ignore[import-not-found]

            self.redis_client = Redis.from_url(redis_url, decode_responses=True)
            logger.info("MCP context: Redis client инициализирован (%s)", _safe_dsn(redis_url))
        except Exception as exc:
            logger.error(
                "Redis инициализация упала — продолжаем без rate-limit (%s)",
                safe_exception_diagnostic(exc),
            )
            self.redis_client = None

        if self.enable_meta_api:
            self.meta_api_client = await _build_meta_api_client()

        self._entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if not self._entered:
            return
        if self.meta_api_client is not None:
            with suppress(Exception):
                await self.meta_api_client.close()
        if self.redis_client is not None:
            with suppress(Exception):
                await self.redis_client.aclose()
        if self.engine is not None:
            with suppress(Exception):
                await self.engine.dispose()
        self._entered = False

    def build_tool_context(self) -> ToolContext:
        """Собрать ToolContext для текущего вызова tool'а.

        client_key=MCP_CLIENT_KEY стабилен — все запросы попадают в один
        rate-limit ведро. requested_by пустой → effective_requested_by()
        вернёт "ai:mcp:claude-desktop" для записи в task_queue.
        """
        return ToolContext(
            client_key=MCP_CLIENT_KEY,
            engine=self.engine,
            redis_client=self.redis_client,
            meta_api_client=self.meta_api_client,
        )


def _safe_dsn(dsn: str) -> str:
    """Return a DSN safe for logs: no credentials, query or fragment."""

    try:
        parsed = urlsplit(dsn)
        if parsed.scheme == "sqlite" and not parsed.query and not parsed.fragment:
            return dsn
        if not parsed.scheme or not parsed.hostname:
            return "<dsn>"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))
    except (TypeError, ValueError):
        return "<dsn>"


__all__ = ["MCPContextManager", "MCP_CLIENT_KEY"]
