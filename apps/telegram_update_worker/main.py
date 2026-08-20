# -*- coding: utf-8 -*-
"""Process durable Telegram webhook updates without long polling or Redis truth."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from core.db import make_worker_engine
from core.telegram.command_replies import (
    DurableTelegramUpdateClient,
    finalize_update_with_replies,
)
from core.telegram.gateway import TelegramGatewayError, TelegramHTMLGateway
from core.telegram.handlers.router import handle_update
from core.telegram.notifications import verify_telegram_authentication
from core.telegram.outbound_authority import hold_telegram_outbound_authority
from core.telegram.service import load_telegram_config
from core.telegram.update_inbox import (
    claim_telegram_update,
    mark_telegram_update_failed,
    reconcile_expired_update_leases,
    release_telegram_update_claim_for_gateway_refresh,
    retire_stale_telegram_update_claim,
    telegram_update_claim_is_authoritative,
)
from core.worker_liveness import record_worker_heartbeat
from core.worker_metrics import mark_worker_heartbeat, start_worker_metrics_server

logger = logging.getLogger(__name__)
WORKER_NAME = "telegram_updates"


async def process_one_update(
    engine: AsyncEngine,
    *,
    gateway: TelegramHTMLGateway,
    worker_id: str,
) -> bool | None:
    claim = await claim_telegram_update(engine, worker_id=worker_id)
    if claim is None:
        return False
    if not await telegram_update_claim_is_authoritative(engine, claim=claim):
        await retire_stale_telegram_update_claim(engine, claim=claim)
        return None
    handler_client = DurableTelegramUpdateClient(gateway)
    try:
        async with hold_telegram_outbound_authority(
            engine,
            bot_generation=claim.bot_generation,
            credential_fingerprint=gateway.credential_fingerprint,
        ) as outbound_authorized:
            if not outbound_authorized:
                retired = await retire_stale_telegram_update_claim(engine, claim=claim)
                if not retired:
                    await release_telegram_update_claim_for_gateway_refresh(
                        engine,
                        claim=claim,
                    )
                # None tells the loop to bypass the 30s gateway cache now.
                return None
            # sendMessage calls are collected in-memory.  Idempotent callback
            # acknowledgements use the cached gateway while this DB authority
            # lock fences token DELETE/rotation through the network boundary.
            await handle_update(
                engine=engine,
                client=handler_client,
                update=claim.payload,
                bot_generation=claim.bot_generation,
            )
    except TelegramGatewayError as exc:
        logger.warning(
            "Telegram inbox Bot API failure update_id=%s kind=%s",
            claim.update_id,
            exc.kind.value,
        )
        await mark_telegram_update_failed(
            engine,
            claim=claim,
            error_code=f"telegram_{exc.kind.value}",
            error_detail="Telegram Bot API request failed",
            gateway_error=exc,
            credential_fingerprint=gateway.credential_fingerprint,
        )
        return True
    except Exception as exc:  # durable retry; never log update payload or callback token
        error_type = type(exc).__name__
        logger.error(
            "Telegram inbox update failed update_id=%s error_type=%s",
            claim.update_id,
            error_type,
        )
        await mark_telegram_update_failed(
            engine,
            claim=claim,
            error_code=error_type,
            error_detail="Telegram update handler failed",
        )
        return True
    finalized = await finalize_update_with_replies(
        engine,
        claim=claim,
        replies=handler_client.replies,
    )
    if not finalized:
        logger.error("Telegram inbox lease lost update_id=%s", claim.update_id)
    return True


async def run_worker(*, engine: AsyncEngine | None = None) -> None:
    owns_engine = engine is None
    if engine is None:
        engine = make_worker_engine(get_settings().database_url)
    start_worker_metrics_server(WORKER_NAME)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass
    gateway: TelegramHTMLGateway | None = None
    auth_ready = False
    active_token = ""
    active_generation = 0
    next_config_refresh = 0.0
    next_reconcile = 0.0
    try:
        while not shutdown.is_set():
            mark_worker_heartbeat(WORKER_NAME)
            await record_worker_heartbeat(engine, WORKER_NAME)
            now = loop.time()
            if gateway is None or now >= next_config_refresh:
                config = await load_telegram_config(engine)
                token = config.bot_token if config is not None else ""
                active_generation = config.webhook_generation if config is not None else 0
                if token != active_token:
                    if gateway is not None:
                        await gateway.close()
                    gateway = TelegramHTMLGateway(token) if token else None
                    active_token = token
                auth_ready = (
                    await verify_telegram_authentication(
                        engine,
                        gateway=gateway,
                        gateway_generation=active_generation,
                    )
                    if gateway is not None
                    else False
                )
                next_config_refresh = now + 30.0
            if now >= next_reconcile:
                await reconcile_expired_update_leases(engine)
                next_reconcile = now + 30.0
            if gateway is None or not auth_ready:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                continue
            processed = await process_one_update(
                engine,
                gateway=gateway,
                worker_id=worker_id,
            )
            # Единственное доказательство, что цикл реально забирает
            # обновления, а не просто числится живым по heartbeat выше
            # (issue #176).
            await record_worker_heartbeat(engine, WORKER_NAME, poll_success=True)
            if processed is None:
                next_config_refresh = 0.0
                continue
            if not processed:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
    finally:
        shutdown.set()
        if gateway is not None:
            await gateway.close()
        if owns_engine:
            await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()


__all__ = ["process_one_update", "run_worker"]
