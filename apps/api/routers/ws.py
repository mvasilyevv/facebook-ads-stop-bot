# -*- coding: utf-8 -*-
"""DB-authoritative operator WebSocket with PostgreSQL reconciliation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.operator.queries import fetch_operator_revision

logger = logging.getLogger(__name__)

# Интервал heartbeat-пинга (секунды). Задаётся через env для тестов.
_HEARTBEAT_SECONDS: int = int(os.environ.get("WS_HEARTBEAT_SECONDS", "30"))

router = APIRouter(tags=["websocket"])


def _websocket_api_key(websocket: WebSocket) -> str:
    """Read only the server-injected handshake header.

    Query-string credentials leak through browser history, proxies and access
    logs.  Production Caddy injects this header after panel authentication;
    TMA uses its independently signed websocket subprotocol instead.
    """
    return websocket.headers.get("x-api-key") or ""


def _websocket_tma_session(websocket: WebSocket) -> str:
    """Read a signed TMA session from a subprotocol, never from the URL."""
    offered = websocket.headers.get("sec-websocket-protocol") or ""
    for protocol in (item.strip() for item in offered.split(",")):
        if protocol.startswith("tma.") and len(protocol) > 4:
            return protocol[4:]
    return ""


def _now_iso() -> str:
    """Текущее время UTC в ISO-8601."""
    return datetime.now(UTC).isoformat()


def _operator_event_scope(payload: dict[str, Any]) -> str:
    """Encode targetable non-action projections without trusting NOTIFY data."""
    scope = str(payload.get("scope") or "snapshot")
    if scope != "campaign_run":
        return scope
    try:
        run_id = str(uuid.UUID(str(payload.get("id") or "")))
    except (ValueError, AttributeError):
        return "campaign_run"
    return f"campaign_run:{run_id}"


async def _validate_tma_websocket_session(session: str, settings: Any) -> bool:
    """Validate the signed session and re-read the active recipient from DB."""
    if not session:
        return False

    from core.auth.tma import InvalidInitDataError, verify_session_token
    from core.config import reveal_secret
    from core.db import get_engine
    from core.telegram.service import (
        find_recipient_by_telegram_user_id,
        telegram_generation_is_authoritative,
    )

    configured_secret = settings.tma_session_secret
    secret = reveal_secret(configured_secret) if configured_secret else ""
    if not secret:
        return False
    try:
        payload = verify_session_token(
            session,
            secret,
            settings.tma_session_ttl_seconds,
        )
        telegram_user_id = int(payload.get("telegram_user_id", 0) or 0)
        bot_generation = int(payload.get("bot_generation", 0) or 0)
    except (InvalidInitDataError, TypeError, ValueError):
        return False
    if telegram_user_id <= 0 or bot_generation <= 0:
        return False
    engine = get_engine()
    if not await telegram_generation_is_authoritative(
        engine,
        bot_generation=bot_generation,
    ):
        return False
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=telegram_user_id)
    # The operator stream exposes the same data as owner-only TMA HTTP
    # endpoints.  Merely being an active notification recipient is not an
    # authorization grant, and a role downgrade must take effect on the next
    # periodic revalidation of an already-open socket.
    return recipient is not None and recipient.is_owner()


async def _authorize_websocket(websocket: WebSocket, *, route: str) -> bool:
    """Apply the same fail-closed API-key policy to every websocket route."""
    from core.config import get_settings as _get_settings
    from core.config import reveal_secret

    settings = _get_settings()
    if not settings.require_api_key:
        return True

    expected = reveal_secret(settings.api_key) if settings.api_key else ""
    provided = _websocket_api_key(websocket)
    if expected and provided and secrets.compare_digest(provided, expected):
        return True

    tma_session = _websocket_tma_session(websocket)
    if await _validate_tma_websocket_session(tma_session, settings):
        return True

    logger.warning("WS %s: отклонён до accept (нет валидной panel/TMA session)", route)
    await websocket.close(code=1008)
    return False


@router.websocket("/ws/operator")
async def ws_operator(websocket: WebSocket) -> None:  # noqa: C901
    """DB-authoritative operator stream with PostgreSQL NOTIFY acceleration.

    ``sequence`` is connection-local and strictly contiguous.  The committed
    database ``snapshot_revision`` is the cross-connection cursor.  A client
    reconciles once when it reconnects, observes a sequence gap, or receives
    ``reconcile_required``.  NOTIFY is never treated as the source of truth:
    every event and every heartbeat are checked against PostgreSQL first.
    """
    if not await _authorize_websocket(websocket, route="/ws/operator"):
        return

    from core.config import get_settings as _get_settings
    from core.db import get_engine

    engine = get_engine()
    settings = _get_settings()
    injected_connection: Any | None = getattr(websocket.app.state, "operator_pg_connection", None)
    own_connection = injected_connection is None
    connection: Any | None = injected_connection
    notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()

    def _termination_listener(_connection: Any) -> None:
        """Force client reconciliation when the dedicated LISTEN socket dies."""

        def _enqueue_termination() -> None:
            try:
                notifications.put_nowait({"_listener_terminated": True})
            except asyncio.QueueFull:
                while not notifications.empty():
                    try:
                        notifications.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                notifications.put_nowait({"_listener_terminated": True})

        loop.call_soon_threadsafe(_enqueue_termination)

    def _listener(_connection: Any, _pid: int, _channel: str, raw_payload: str) -> None:
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
            if not isinstance(payload, dict):
                payload = {"scope": "snapshot"}
        except (TypeError, json.JSONDecodeError):
            payload = {"scope": "snapshot"}

        def _enqueue() -> None:
            try:
                notifications.put_nowait(payload)
            except asyncio.QueueFull:
                while not notifications.empty():
                    try:
                        notifications.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                notifications.put_nowait({"scope": "snapshot", "overflow": True})

        loop.call_soon_threadsafe(_enqueue)

    try:
        if connection is None:
            dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            connection = await asyncpg.connect(dsn=dsn, timeout=5)
        await connection.add_listener("fb_operator_events", _listener)
        # asyncpg does not transparently restore LISTEN registrations after a
        # terminated connection.  Closing the browser socket with 1013 makes
        # the typed client reconnect and perform its mandatory snapshot
        # reconciliation instead of silently degrading to heartbeat polling.
        if hasattr(connection, "add_termination_listener"):
            connection.add_termination_listener(_termination_listener)
        _source_sequence, revision = await fetch_operator_revision(engine)
    except Exception as exc:  # noqa: BLE001 - websocket must fail closed
        logger.error("WS /ws/operator: PostgreSQL listener unavailable: %s", type(exc).__name__)
        if own_connection and connection is not None:
            await connection.close()
        await websocket.close(code=1013)
        return

    await websocket.accept()
    sequence = 1
    last_revision = revision
    tma_session = _websocket_tma_session(websocket)
    clock = asyncio.get_running_loop()
    next_tma_revalidation = clock.time() + _HEARTBEAT_SECONDS
    await websocket.send_json(
        {
            "type": "snapshot_required",
            "sequence": sequence,
            "snapshot_revision": revision,
            "scopes": ["snapshot"],
            "ts": _now_iso(),
        }
    )

    try:
        while True:
            try:
                auth_wait = (
                    max(0.05, next_tma_revalidation - clock.time())
                    if tma_session
                    else _HEARTBEAT_SECONDS
                )
                first = await asyncio.wait_for(
                    notifications.get(),
                    timeout=min(_HEARTBEAT_SECONDS, auth_wait),
                )
                batch = [first]
                while not notifications.empty():
                    batch.append(notifications.get_nowait())

                if any(item.get("_listener_terminated") for item in batch):
                    await websocket.close(code=1013)
                    return

                if tma_session and clock.time() >= next_tma_revalidation:
                    if not await _validate_tma_websocket_session(tma_session, settings):
                        await websocket.close(code=1008)
                        return
                    next_tma_revalidation = clock.time() + _HEARTBEAT_SECONDS
                _source_sequence, current_revision = await fetch_operator_revision(engine)
                scopes = {_operator_event_scope(item) for item in batch if isinstance(item, dict)}
                overflow = any(bool(item.get("overflow")) for item in batch)
                sequence += 1
                last_revision = current_revision
                await websocket.send_json(
                    {
                        "type": "reconcile_required" if overflow else "changed",
                        "sequence": sequence,
                        "snapshot_revision": current_revision,
                        "scopes": ["snapshot"] if overflow else sorted(scopes),
                        "ts": _now_iso(),
                    }
                )
            except TimeoutError:
                # Heartbeat doubles as the mandatory DB reconciliation for a
                # lost NOTIFY or a PostgreSQL listener reconnect edge.
                if tma_session and clock.time() >= next_tma_revalidation:
                    if not await _validate_tma_websocket_session(tma_session, settings):
                        await websocket.close(code=1008)
                        return
                    next_tma_revalidation = clock.time() + _HEARTBEAT_SECONDS
                _source_sequence, current_revision = await fetch_operator_revision(engine)
                sequence += 1
                if current_revision != last_revision:
                    last_revision = current_revision
                    await websocket.send_json(
                        {
                            "type": "reconcile_required",
                            "sequence": sequence,
                            "snapshot_revision": current_revision,
                            "scopes": ["snapshot"],
                            "ts": _now_iso(),
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "ping",
                            "sequence": sequence,
                            "snapshot_revision": current_revision,
                            "scopes": [],
                            "ts": _now_iso(),
                        }
                    )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001 - connection cleanup still required
        logger.info("WS /ws/operator closed: %s", type(exc).__name__)
    finally:
        try:
            if connection is not None:
                await connection.remove_listener("fb_operator_events", _listener)
        except Exception:
            pass
        try:
            if connection is not None and hasattr(connection, "remove_termination_listener"):
                connection.remove_termination_listener(_termination_listener)
        except Exception:
            pass
        if own_connection and connection is not None:
            try:
                await connection.close()
            except Exception:
                pass
