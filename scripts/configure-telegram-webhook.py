#!/usr/bin/env python3
"""Reconcile one durable Telegram webhook generation for release cutover."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from core.config import get_settings
from core.db import get_engine
from core.telegram.service import load_telegram_config
from core.telegram.webhook_configuration import (
    ensure_webhook_configuration_desired,
    process_one_webhook_configuration,
    resolve_webhook_target,
)


async def configure() -> None:
    settings = get_settings()
    engine = get_engine()
    config = await load_telegram_config(engine)
    if config is None or not config.bot_token:
        raise RuntimeError("telegram_config has no active bot token")
    target = resolve_webhook_target(
        frontend_origin=settings.frontend_origin,
        secret_token=settings.telegram_webhook_secret,
    )
    # Every release performs a fresh remote setWebhook + getWebhookInfo proof.
    # A lost response remains a durable retry row; this one-shot exits non-zero
    # so production deployment cannot call it success prematurely.
    await ensure_webhook_configuration_desired(engine, target=target, force=True)
    if not await process_one_webhook_configuration(
        engine,
        worker_id="release-configurator",
    ):
        raise RuntimeError("Telegram webhook generation was not claimable")
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT webhook_state, webhook_generation,
                           webhook_applied_generation, webhook_desired_url,
                           webhook_remote_url, webhook_last_error_code
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).first()
    if (
        row is None
        or row.webhook_state != "configured"
        or row.webhook_applied_generation != row.webhook_generation
        or row.webhook_desired_url != target.url
        or row.webhook_remote_url != target.url
    ):
        code = row.webhook_last_error_code if row is not None else "missing_config"
        raise RuntimeError(f"Telegram webhook generation is not remotely confirmed ({code})")


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    asyncio.run(configure())
    print("Telegram webhook configured")
