#!/usr/bin/env python3
"""Import explicit runtime secrets/config after schema and adoption succeed."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from core.adset_pro.credentials import bootstrap_adsetpro_credentials_from_env
from core.config import get_settings
from core.telegram.service import bootstrap_telegram_config_from_env
from core.telegram.web_app_url import bootstrap_web_app_url_from_env


async def _run() -> None:
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        await bootstrap_telegram_config_from_env(engine)
        await bootstrap_adsetpro_credentials_from_env(engine)
        await bootstrap_web_app_url_from_env(engine)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception:
        print("runtime configuration bootstrap failed", file=sys.stderr)
        return 1
    print("runtime configuration bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
