from __future__ import annotations

import logging

from apps.api.config import ApiSettings
from core.db import get_session_factory
from core.repositories import RulesRepository


async def bootstrap_reference_data(settings: ApiSettings) -> None:
    """Поднимает системные справочники, без которых API работает неполноценно."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = RulesRepository(session)
        await repo.ensure_default_rules()
        await session.commit()
    logging.getLogger(__name__).info(
        "Справочные данные API синхронизированы для окружения %s",
        settings.environment,
    )
