# -*- coding: utf-8 -*-
"""Dependency injection для FastAPI.

Принципы:
- `get_settings` возвращает синглтон из `core.config.get_settings`.
- `get_engine` использует уже существующий ленивый синглтон из `core.db`.
- `get_redis` берёт клиент из `request.app.state.redis` — заводится в lifespan.
  Тесты могут переопределить `app.state.redis` (например, на fakeredis).
- `get_adset_pro_client` — async generator (async-context-manager), закрывает
  HTTP-клиент после использования.

DI явно прописан здесь, чтобы:
- роутеры легко мокались через `app.dependency_overrides`;
- тесты могли подменять Postgres/Redis без обращения к глобальным синглтонам.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro import AdsetProClient
from core.adset_pro.credentials import create_adsetpro_client
from core.config import Settings
from core.config import get_settings as _core_get_settings
from core.db import get_engine as _core_get_engine
from core.meta_api.client import MetaApiClient


def get_settings() -> Settings:
    """Синглтон настроек."""
    return _core_get_settings()


def get_engine() -> AsyncEngine:
    """Async-engine SQLAlchemy (синглтон из core.db)."""
    return _core_get_engine()


async def get_redis(request: Request) -> Redis:
    """Redis-клиент из app.state. Заводится в lifespan."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("Redis не инициализирован в app.state (lifespan не отработал)")
    return redis


def get_meta_api_client(request: Request) -> MetaApiClient | None:
    """Общий gRPC-клиент browser-agent из API lifespan (может быть недоступен)."""
    return getattr(request.app.state, "meta_api_client", None)


# Annotated-алиасы для удобного использования в роутерах v1:
#   async def my_handler(engine: DepEngine, redis: DepRedis, s: DepSettings): ...
DepEngine = Annotated[AsyncEngine, Depends(get_engine)]
DepRedis = Annotated[Redis, Depends(get_redis)]
DepSettings = Annotated[Settings, Depends(get_settings)]
DepMetaApiClient = Annotated[MetaApiClient | None, Depends(get_meta_api_client)]


async def get_adset_pro_client(
    engine: AsyncEngine = Depends(get_engine),
) -> AsyncIterator[AdsetProClient]:
    """Async generator: создаёт `AdsetProClient`, отдаёт его, закрывает после.

    Ключ резолвится из БД (adsetpro_credentials) с фолбэком на .env — ротация
    без рестарта (см. core.adset_pro.credentials).
    """
    client = await create_adsetpro_client(engine)
    await client.start()
    try:
        yield client
    finally:
        await client.close()
