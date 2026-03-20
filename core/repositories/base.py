from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class AsyncRepository:
    """Базовый класс для асинхронных репозиториев."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
