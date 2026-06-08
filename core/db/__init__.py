# -*- coding: utf-8 -*-
"""Инициализация SQLAlchemy async-движка и фабрики сессий."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# Консервативный пул для ВОРКЕР-процессов (H-10/A2). Каждый воркер — отдельный
# процесс с одним async-циклом (+ heartbeat), нужно 1-2 коннекта. Дефолт SQLAlchemy
# (pool_size 5 + max_overflow 10 = 15/движок) × ~14 процессов ≈ 210 против Postgres
# max_connections=100 → «too many clients» при рестартах/нагрузке. 2+2=4/процесс ×
# ~14 ≈ 56 + API(30, get_engine) + запас. pool_pre_ping/recycle — против протухших
# коннектов (воркеры живут сутками). API остаётся на get_engine (свой больший пул).
WORKER_ENGINE_KWARGS: dict = {
    "echo": False,
    "pool_size": 2,
    "max_overflow": 2,
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}


def make_worker_engine(url: str) -> AsyncEngine:
    """Async-движок воркера с консервативным пулом (см. WORKER_ENGINE_KWARGS)."""
    return create_async_engine(url, **WORKER_ENGINE_KWARGS)


def get_engine():
    """Ленивый синглтон движка SQLAlchemy."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,  # переподключение раз в час
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Ленивый синглтон фабрики сессий."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory
