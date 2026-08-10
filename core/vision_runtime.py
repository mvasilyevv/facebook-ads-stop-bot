"""Canonical runtime configuration for the Vision browser channel.

Vision credentials are operator-managed data and live only in PostgreSQL.
Deployment environment variables configure transport endpoints, not a second
credential authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.crypto import decrypt
from core.models.settings.vision_config import VisionConfig


class VisionConfigurationError(RuntimeError):
    """Raised when the canonical Vision configuration cannot be used."""


@dataclass(frozen=True, slots=True)
class VisionRuntimeConfig:
    """Decrypted, detached Vision configuration used by runtime clients."""

    x_token: str = field(repr=False)
    profile_id: str
    configuration_revision: str


async def load_vision_runtime_config(engine: AsyncEngine) -> VisionRuntimeConfig:
    """Load the single PostgreSQL Vision configuration or fail closed."""
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                select(
                    VisionConfig.x_token_encrypted,
                    VisionConfig.profile_id,
                    VisionConfig.updated_at,
                ).where(VisionConfig.singleton_key == "default")
            )
        ).one_or_none()
        if row is None:
            raise VisionConfigurationError("Vision is not configured in PostgreSQL")
        encrypted_token = (row.x_token_encrypted or "").strip()
        profile_id = (row.profile_id or "").strip()
        configuration_revision = row.updated_at.isoformat()

    if not encrypted_token:
        raise VisionConfigurationError("Vision token is not configured in PostgreSQL")
    if not profile_id:
        raise VisionConfigurationError("Vision profile is not configured in PostgreSQL")
    try:
        x_token = decrypt(encrypted_token).strip()
    except Exception as exc:
        raise VisionConfigurationError("Vision token cannot be decrypted") from exc
    if not x_token:
        raise VisionConfigurationError("Vision token is empty")
    return VisionRuntimeConfig(
        x_token=x_token,
        profile_id=profile_id,
        configuration_revision=configuration_revision,
    )


__all__ = [
    "VisionConfigurationError",
    "VisionRuntimeConfig",
    "load_vision_runtime_config",
]
