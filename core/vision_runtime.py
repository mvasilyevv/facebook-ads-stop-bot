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
    folder_id: str | None = field(default=None, repr=False)


async def load_vision_runtime_config(engine: AsyncEngine) -> VisionRuntimeConfig:
    """Load the single PostgreSQL Vision configuration or fail closed."""
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                select(
                    VisionConfig.x_token_encrypted,
                    VisionConfig.profile_id,
                    VisionConfig.folder_id_encrypted,
                    VisionConfig.updated_at,
                ).where(VisionConfig.singleton_key == "default")
            )
        ).one_or_none()
        if row is None:
            raise VisionConfigurationError("Vision is not configured in PostgreSQL")
        encrypted_token = (row.x_token_encrypted or "").strip()
        profile_id = (row.profile_id or "").strip()
        encrypted_folder_id = (row.folder_id_encrypted or "").strip()
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
    folder_id: str | None = None
    if encrypted_folder_id:
        try:
            folder_id = decrypt(encrypted_folder_id).strip() or None
        except Exception as exc:
            raise VisionConfigurationError("Vision folder cannot be decrypted") from exc
        if folder_id is None:
            raise VisionConfigurationError("Vision folder cannot be decrypted")
    return VisionRuntimeConfig(
        x_token=x_token,
        profile_id=profile_id,
        folder_id=folder_id,
        configuration_revision=configuration_revision,
    )


__all__ = [
    "VisionConfigurationError",
    "VisionRuntimeConfig",
    "load_vision_runtime_config",
]
