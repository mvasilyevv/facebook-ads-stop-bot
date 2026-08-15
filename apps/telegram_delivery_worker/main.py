# -*- coding: utf-8 -*-
"""Lease-fenced HTML Telegram delivery worker."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from core.db import make_worker_engine
from core.safe_diagnostics import safe_exception_diagnostic
from core.telegram.action_tokens import mint_action_token
from core.telegram.command_replies import (
    ClaimedTelegramCommandReply,
    claim_telegram_command_reply,
    mark_command_reply_external_started,
    mark_command_reply_failure,
    mark_command_reply_sent,
    reconcile_expired_command_reply_leases,
)
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    TelegramHTMLGateway,
)
from core.telegram.navigation_tokens import mint_navigation_token
from core.telegram.notification_renderer import render_notification
from core.telegram.notifications import (
    ClaimedNotificationDelivery,
    build_incident_reissue_spec,
    claim_notification_delivery,
    mark_delivery_external_started,
    mark_delivery_failure,
    mark_delivery_sent,
    mark_delivery_superseded,
    reconcile_expired_delivery_leases,
    refresh_notification_metrics,
    verify_telegram_authentication,
)
from core.telegram.outbound_authority import hold_telegram_outbound_authority
from core.telegram.service import load_telegram_config
from core.telegram.web_app_url import load_web_app_url, normalize_web_app_base
from core.telegram.webhook_configuration import process_one_webhook_configuration
from core.worker_metrics import (
    mark_worker_heartbeat,
    record_notification_delivery_transition,
    start_worker_metrics_server,
)

logger = logging.getLogger(__name__)
WORKER_NAME = "telegram_delivery"

_EDIT_TARGET_MISSING = (
    "message to edit not found",
    "message can't be edited",
    "there is no text in the message to edit",
)


class LostDeliveryLeaseError(RuntimeError):
    """Capability preparation was fenced out before it could mint tokens."""


def _recipient_can_use_action(*, recipient_role: str, required_role: str) -> bool:
    """Filter capabilities before minting so every rendered button is executable."""
    return required_role == "recipient" or recipient_role == "owner"


async def process_one_command_reply(
    engine: AsyncEngine,
    *,
    gateway: TelegramHTMLGateway,
    gateway_generation: int,
    worker_id: str,
) -> bool:
    """Deliver one committed command reply through a fenced send boundary."""
    claim: ClaimedTelegramCommandReply | None = await claim_telegram_command_reply(
        engine,
        worker_id=worker_id,
        gateway_generation=gateway_generation,
        credential_fingerprint=gateway.credential_fingerprint,
    )
    if claim is None:
        return False
    try:
        async with hold_telegram_outbound_authority(
            engine,
            bot_generation=claim.bot_generation,
            credential_fingerprint=gateway.credential_fingerprint,
        ) as authorized:
            boundary_ready = await mark_command_reply_external_started(
                engine,
                claim=claim,
                gateway_generation=gateway_generation,
                credential_fingerprint=gateway.credential_fingerprint,
            )
            if not authorized or not boundary_ready:
                logger.info("command reply lease lost before send reply=%s", claim.reply_id)
                return True
            sent = await gateway.send_message(
                chat_id=claim.chat_id,
                text=claim.text,
                parse_mode=claim.parse_mode,
                reply_to_message_id=claim.reply_to_message_id,
                reply_markup=claim.reply_markup,
            )
        message_id = int(sent["message_id"])
    except TelegramGatewayError as error:
        await mark_command_reply_failure(
            engine,
            claim=claim,
            error=error,
            credential_fingerprint=gateway.credential_fingerprint,
        )
        return True
    except (KeyError, TypeError, ValueError):
        # The intent was already committed.  Invalid persisted content cannot
        # become valid on retry and must not poison the delivery worker loop.
        await mark_command_reply_failure(
            engine,
            claim=claim,
            error=TelegramGatewayError(
                method="sendMessage",
                kind=TelegramFailureKind.INVALID_REQUEST,
                description="invalid durable command reply",
            ),
            credential_fingerprint=gateway.credential_fingerprint,
        )
        return True
    if not await mark_command_reply_sent(engine, claim=claim, message_id=message_id):
        logger.error("command reply result could not be lease-finalized reply=%s", claim.reply_id)
    return True


async def _mint_delivery_capabilities(
    engine: AsyncEngine,
    claim: ClaimedNotificationDelivery,
) -> tuple[
    dict[str, str],
    str | None,
    tuple[uuid.UUID, ...],
    tuple[uuid.UUID, ...],
]:
    callbacks: dict[str, str] = {}
    action_token_ids: list[uuid.UUID] = []
    navigation_token_ids: list[uuid.UUID] = []
    raw_base = await load_web_app_url(engine)
    web_app_base = normalize_web_app_base(raw_base)
    navigation_url: str | None = None
    async with engine.begin() as conn:
        lease = (
            await conn.execute(
                text(
                    """
                    SELECT NOW() AS policy_now
                    FROM notification_deliveries
                    WHERE id = :delivery_id
                      AND state = 'leased'
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                    """
                ),
                {
                    "delivery_id": claim.delivery_id,
                    "lease_token": claim.lease_token,
                },
            )
        ).first()
        if lease is None:
            raise LostDeliveryLeaseError("delivery lease expired before capability mint")
        policy_now = lease.policy_now
        for action in claim.event.actions:
            if not _recipient_can_use_action(
                recipient_role=claim.recipient_role,
                required_role=action.required_role,
            ):
                continue
            issued = await mint_action_token(
                conn,
                recipient_id=claim.recipient_id,
                delivery_id=claim.delivery_id,
                event_id=claim.event_id,
                incident_id=claim.incident_id,
                action_key=action.key,
                action_kind=action.kind,
                target_type=action.target_type,
                target_id=action.target_id,
                target_payload=action.target_payload,
                required_role=action.required_role,
                incident_generation=claim.incident_generation,
                expires_at=policy_now + timedelta(seconds=action.expires_in_seconds),
            )
            callbacks[action.key] = issued.callback_data
            action_token_ids.append(issued.id)
        target = claim.event.facts.open_target
        if target is not None and web_app_base is not None:
            issued_navigation = await mint_navigation_token(
                conn,
                recipient_id=claim.recipient_id,
                delivery_id=claim.delivery_id,
                event_id=claim.event_id,
                target_kind=target.kind,
                target_id=target.target_id,
                expires_at=policy_now + timedelta(hours=24),
            )
            separator = "&" if "?" in web_app_base else "?"
            navigation_url = f"{web_app_base}{separator}nav={issued_navigation.raw_token}"
            navigation_token_ids.append(issued_navigation.id)
    return (
        callbacks,
        navigation_url,
        tuple(action_token_ids),
        tuple(navigation_token_ids),
    )


def _is_unchanged(error: TelegramGatewayError) -> bool:
    return "message is not modified" in error.description.lower()


def _is_missing_edit_target(error: TelegramGatewayError) -> bool:
    description = error.description.lower()
    return any(marker in description for marker in _EDIT_TARGET_MISSING)


async def process_one_delivery(
    engine: AsyncEngine,
    *,
    gateway: TelegramHTMLGateway,
    gateway_generation: int,
    worker_id: str,
) -> bool:
    """Claim and process one delivery. Return False when the queue is empty."""
    claim = await claim_notification_delivery(
        engine,
        worker_id=worker_id,
        gateway_generation=gateway_generation,
        credential_fingerprint=gateway.credential_fingerprint,
    )
    if claim is None:
        return False
    try:
        (
            callbacks,
            navigation_url,
            active_action_token_ids,
            active_navigation_token_ids,
        ) = await _mint_delivery_capabilities(engine, claim)
        rendered = render_notification(
            claim.event,
            action_callbacks=callbacks,
            navigation_url=navigation_url,
        )
    except LostDeliveryLeaseError:
        logger.info(
            "notification capability preparation fenced out delivery=%s",
            claim.delivery_id,
        )
        return True
    except Exception as exc:
        logger.error(
            "notification render preparation failed delivery=%s (%s)",
            claim.delivery_id,
            safe_exception_diagnostic(exc),
        )
        error = TelegramGatewayError(
            method="prepareNotification",
            kind=TelegramFailureKind.TRANSIENT,
            description="notification preparation failed",
        )
        decision = await mark_delivery_failure(engine, claim=claim, error=error)
        if decision.finalized:
            record_notification_delivery_transition(
                decision.state,
                claim.event.severity,
                event_created_at=claim.event_created_at,
            )
        return True

    message_id = claim.slot_message_id
    force_send = claim.event.event_type == "incident_snapshot_reissued"
    operation_kind = "edit" if message_id is not None and not force_send else "send"
    try:
        async with hold_telegram_outbound_authority(
            engine,
            bot_generation=claim.bot_generation,
            credential_fingerprint=gateway.credential_fingerprint,
        ) as authorized:
            external_state = await mark_delivery_external_started(
                engine,
                claim=claim,
                operation_kind=operation_kind,
                gateway_generation=gateway_generation,
                credential_fingerprint=gateway.credential_fingerprint,
            )
            if not authorized or external_state != "ready":
                logger.info(
                    "notification external call skipped delivery=%s state=%s",
                    claim.delivery_id,
                    external_state,
                )
                return True

            replacement_tokens_confirmed = True
            if message_id is not None and not force_send:
                await gateway.edit_message(
                    chat_id=claim.chat_id,
                    message_id=message_id,
                    text=rendered.text,
                    reply_markup=rendered.reply_markup,
                )
            else:
                sent = await gateway.send_message(
                    chat_id=claim.chat_id,
                    text=rendered.text,
                    reply_markup=rendered.reply_markup,
                )
                message_id = int(sent["message_id"])
    except TelegramGatewayError as error:
        if message_id is not None and _is_unchanged(error):
            # Telegram did not install this attempt's freshly minted buttons.
            # Keep every capability already visible in the message active.
            replacement_tokens_confirmed = False
        elif message_id is not None and _is_missing_edit_target(error):
            if claim.incident_id is None:
                decision = await mark_delivery_failure(
                    engine,
                    claim=claim,
                    error=TelegramGatewayError(
                        method=error.method,
                        kind=TelegramFailureKind.INVALID_REQUEST,
                        description="missing edit target without incident lifecycle",
                    ),
                )
                if decision.finalized:
                    record_notification_delivery_transition(
                        decision.state,
                        claim.event.severity,
                        event_created_at=claim.event_created_at,
                    )
                return True
            reissue = build_incident_reissue_spec(
                source_event=claim.event,
                source_event_id=claim.event_id,
                recipient_id=claim.recipient_id,
                incident_id=claim.incident_id,
                incident_generation=claim.incident_generation,
                incident_status=claim.incident_status,
            )
            finalized = await mark_delivery_superseded(
                engine,
                claim=claim,
                reason="Telegram edit target is unavailable; explicit reissue enqueued",
                reissue=reissue,
            )
            if finalized:
                record_notification_delivery_transition(
                    "superseded",
                    claim.event.severity,
                    event_created_at=claim.event_created_at,
                )
            return True
        else:
            decision = await mark_delivery_failure(
                engine,
                claim=claim,
                error=error,
                credential_fingerprint=gateway.credential_fingerprint,
            )
            if decision.finalized:
                record_notification_delivery_transition(
                    decision.state,
                    claim.event.severity,
                    event_created_at=claim.event_created_at,
                )
            return True

    if message_id is None or message_id <= 0:  # gateway already enforces this for send
        error = TelegramGatewayError(
            method="finalizeDelivery",
            kind=TelegramFailureKind.UNKNOWN,
        )
        decision = await mark_delivery_failure(engine, claim=claim, error=error)
        if decision.finalized:
            record_notification_delivery_transition(
                decision.state,
                claim.event.severity,
                event_created_at=claim.event_created_at,
            )
        return True
    finalized = await mark_delivery_sent(
        engine,
        claim=claim,
        message_id=message_id,
        render_hash=rendered.render_hash,
        active_action_token_ids=(active_action_token_ids if replacement_tokens_confirmed else ()),
        active_navigation_token_ids=(
            active_navigation_token_ids if replacement_tokens_confirmed else ()
        ),
    )
    if not finalized:
        logger.error("delivery result could not be lease-finalized delivery=%s", claim.delivery_id)
    else:
        record_notification_delivery_transition(
            "sent",
            claim.event.severity,
            event_created_at=claim.event_created_at,
        )
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
    next_webhook_configuration = 0.0
    next_reconcile = 0.0
    next_metrics_refresh = 0.0
    try:
        while not shutdown.is_set():
            mark_worker_heartbeat(WORKER_NAME)
            now = loop.time()
            if now >= next_webhook_configuration:
                try:
                    webhook_processed = await process_one_webhook_configuration(
                        engine,
                        worker_id=f"{worker_id}:webhook",
                    )
                    if webhook_processed:
                        # The authoritative token or its enabled state may have
                        # changed with the generation we just finalized.
                        next_config_refresh = 0.0
                except Exception as exc:  # noqa: BLE001 - durable row remains claimable
                    logger.error(
                        "durable Telegram webhook configuration pass failed (%s)",
                        safe_exception_diagnostic(exc),
                    )
                next_webhook_configuration = now + 0.5
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
                retry_count, unknown_count = await reconcile_expired_delivery_leases(engine)
                (
                    reply_retry_count,
                    reply_unknown_count,
                ) = await reconcile_expired_command_reply_leases(engine)
                record_notification_delivery_transition("retry", "unknown", count=retry_count)
                record_notification_delivery_transition("unknown", "unknown", count=unknown_count)
                if reply_retry_count or reply_unknown_count:
                    logger.warning(
                        "command reply leases reconciled retry=%d unknown=%d",
                        reply_retry_count,
                        reply_unknown_count,
                    )
                next_reconcile = now + 30.0
            if now >= next_metrics_refresh:
                try:
                    await refresh_notification_metrics(engine)
                except Exception as exc:  # noqa: BLE001 - exporter failure must not halt delivery
                    logger.error(
                        "notification metric refresh failed (%s)",
                        safe_exception_diagnostic(exc),
                    )
                next_metrics_refresh = now + 15.0
            if gateway is None or not auth_ready or active_generation <= 0:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                continue
            notification_processed = await process_one_delivery(
                engine,
                gateway=gateway,
                gateway_generation=active_generation,
                worker_id=worker_id,
            )
            reply_processed = await process_one_command_reply(
                engine,
                gateway=gateway,
                gateway_generation=active_generation,
                worker_id=worker_id,
            )
            if not notification_processed and not reply_processed:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=0.5)
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


__all__ = ["process_one_command_reply", "process_one_delivery", "run_worker"]
