#!/usr/bin/env python3
"""Bootstrap the canonical Vision singleton from one-shot secret input.

The plaintext token is accepted only through the private Compose env file used
by this job.  Existing configuration is never overwritten: an exact plaintext
token/profile match is a no-op and any mismatch fails closed.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.crypto import decrypt, encrypt

_LOCK_ID = 0x46424147454E5456  # "FBAGENTV"


def _required(name: str, *, maximum: int) -> str:
    value = os.environ.get(name, "").strip()
    if not value or len(value) > maximum or "\x00" in value or "\n" in value or "\r" in value:
        raise RuntimeError(f"{name} is missing or invalid")
    return value


async def bootstrap_vision_config(
    engine: AsyncEngine,
    *,
    x_token: str,
    profile_id: str,
    folder_id: str | None = None,
) -> str:
    """Create or verify the singleton and import a legacy folder once."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", profile_id):
        raise RuntimeError("VISION_BOOTSTRAP_PROFILE_ID is invalid")
    if folder_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", folder_id):
        raise RuntimeError("VISION_FOLDER_ID is invalid")
    encrypted_token = encrypt(x_token)
    encrypted_folder = encrypt(folder_id) if folder_id else None
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _LOCK_ID}
        )
        row = (
            await connection.execute(
                text(
                    "SELECT x_token_encrypted, profile_id, folder_id_encrypted "
                    "FROM vision_config WHERE singleton_key = 'default' FOR UPDATE"
                )
            )
        ).one_or_none()
        if row is None:
            await connection.execute(
                text(
                    "INSERT INTO vision_config "
                    "(x_token_encrypted, profile_id, folder_id_encrypted, singleton_key) "
                    "VALUES (:token, :profile, :folder, 'default')"
                ),
                {"token": encrypted_token, "profile": profile_id, "folder": encrypted_folder},
            )
            return "created"
        existing_token, existing_profile, existing_folder = row
        if existing_profile != profile_id or decrypt(existing_token) != x_token:
            raise RuntimeError("canonical Vision configuration conflicts with bootstrap input")
        if folder_id and not existing_folder:
            # VISION_FOLDER_ID остаётся в app env при переходе на 0004. Импортируем
            # его ровно один раз; после этого БД — единственный runtime-источник.
            await connection.execute(
                text(
                    "UPDATE vision_config SET folder_id_encrypted = :folder, "
                    "updated_at = GREATEST(clock_timestamp(), updated_at + INTERVAL '1 microsecond') "
                    "WHERE singleton_key = 'default' AND folder_id_encrypted IS NULL"
                ),
                {"folder": encrypted_folder},
            )
            return "updated"
        return "verified"


async def _run() -> str:
    x_token = _required("VISION_BOOTSTRAP_X_TOKEN", maximum=16_384)
    profile_id = _required("VISION_BOOTSTRAP_PROFILE_ID", maximum=64)
    folder_id = os.environ.get("VISION_FOLDER_ID", "").strip() or None
    if folder_id is not None and (
        len(folder_id) > 128 or "\x00" in folder_id or "\n" in folder_id or "\r" in folder_id
    ):
        raise RuntimeError("VISION_FOLDER_ID is invalid")
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        return await bootstrap_vision_config(
            engine,
            x_token=x_token,
            profile_id=profile_id,
            folder_id=folder_id,
        )
    finally:
        await engine.dispose()


def main() -> int:
    try:
        outcome = asyncio.run(_run())
    except Exception:
        print("Vision configuration bootstrap failed", file=sys.stderr)
        return 1
    print(f"Vision configuration bootstrap {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
