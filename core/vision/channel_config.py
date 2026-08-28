# -*- coding: utf-8 -*-
"""Настроен ли канал Vision: факт наличия учётных данных, без расшифровки.

Отличать «оператор ещё не ввёл токен» от «канал не отвечает» обязан каждый
воркер, который иначе превратит штатный первый запуск в поток аварий. Токен
здесь не расшифровывается: нужен только факт непустого значения.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.models.settings.vision_config import VisionConfig


@dataclass(frozen=True, slots=True)
class VisionChannelConfiguration:
    """Состояние настроенности канала.

    ``has_token`` — в конфигурации лежит непустой зашифрованный токен.
    ``profile_id`` — выбранный оператором профиль, пустая строка означает «не выбран».
    """

    has_token: bool
    profile_id: str

    @property
    def is_configured(self) -> bool:
        """Канал настроен только когда есть и токен, и профиль."""
        return self.has_token and bool(self.profile_id)


async def load_vision_channel_configuration(engine: AsyncEngine) -> VisionChannelConfiguration:
    """Прочитать настроенность канала из ``vision_config``.

    Отсутствие строки конфигурации — это «не настроено», а не отказ: на чистом
    хосте строки нет до первого сохранения настроек оператором.
    """
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                select(
                    VisionConfig.x_token_encrypted,
                    VisionConfig.profile_id,
                ).where(VisionConfig.singleton_key == "default")
            )
        ).one_or_none()
    if row is None:
        return VisionChannelConfiguration(has_token=False, profile_id="")
    return VisionChannelConfiguration(
        has_token=bool(str(row.x_token_encrypted or "").strip()),
        profile_id=str(row.profile_id or "").strip(),
    )


__all__ = [
    "VisionChannelConfiguration",
    "load_vision_channel_configuration",
]
