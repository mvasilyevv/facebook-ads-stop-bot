from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core import models as _models  # noqa: F401
from core.db.base import Base


def _dedupe_metadata_indexes() -> None:
    """Убирает дублирующиеся индексы из metadata для SQLite-интеграции."""

    for table in Base.metadata.tables.values():
        index_names: dict[str, int] = {}
        for index in table.indexes:
            index_names[index.name] = index_names.get(index.name, 0) + 1

        duplicate_names = {name for name, count in index_names.items() if count > 1}
        if duplicate_names:
            for column in table.columns:
                generated_name = f"ix_{table.name}_{column.name}"
                if generated_name in duplicate_names:
                    column.index = False

        seen_names: set[str] = set()
        duplicate_indexes = []
        for index in table.indexes:
            if index.name in seen_names:
                duplicate_indexes.append(index)
            else:
                seen_names.add(index.name)
        for index in duplicate_indexes:
            table.indexes.discard(index)


@pytest.fixture
async def async_engine(tmp_path) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/repositories.db")
    _dedupe_metadata_indexes()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
