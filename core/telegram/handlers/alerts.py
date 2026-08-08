# -*- coding: utf-8 -*-
"""Inline actions backed by recipient-bound opaque capabilities.

Money actions use ``a:<22-char opaque token>`` and reach the canonical
``CommandService``. No raw target or task identifier is accepted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.commands.service import (
    CommandConflictError,
    CommandNotFoundError,
    CommandService,
)
from core.incidents.service import (
    IncidentGenerationMismatchError,
    IncidentNotAcknowledgeableError,
    IncidentNotFoundError,
    acknowledge_incident,
)
from core.telegram.action_tokens import (
    ActionTokenClaim,
    claim_action_token,
    complete_action_token,
    consume_action_token,
)
from core.telegram.handlers.protocol import TelegramUpdateClient
from core.telegram.notifications import serialize_recipient_delivery_state_in_transaction
from core.telegram.owner_roster import lock_owner_roster

logger = logging.getLogger(__name__)


class TelegramActionAuthorityLost(RuntimeError):
    """The bot generation was disabled or rotated before command commit."""


def _command_idempotency_key(claim: ActionTokenClaim) -> str:
    """Bind equivalent incident capabilities to one durable command CAS.

    Telegram edits mint a fresh opaque token. The previous button can still be
    clicked while the edit is in flight, so token identity alone is too narrow
    for money-action idempotency. Incident generation plus semantic target is
    stable across those rotations and across multiple owner recipients.
    """
    if (
        claim.incident_id is not None
        and claim.incident_generation is not None
        and claim.action_kind is not None
        and claim.target_type is not None
        and claim.target_id is not None
    ):
        semantic_action = json.dumps(
            {
                "incident_id": str(claim.incident_id),
                "incident_generation": int(claim.incident_generation),
                "action_kind": claim.action_kind,
                "target_type": claim.target_type,
                "target_id": claim.target_id,
                "target_payload": claim.target_payload or {},
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(semantic_action.encode("utf-8")).hexdigest()
        return f"telegram-incident-action:{digest}"
    if claim.token_id is None:  # pragma: no cover - caller validates first
        raise ValueError("claimed capability has no token identity")
    return f"telegram-action:{claim.token_id}"


async def handle_action_callback(
    *,
    engine: AsyncEngine,
    client: TelegramUpdateClient,
    cq_id: str,
    raw_token: str | None,
    chat_id: int,
    telegram_user_id: int,
    username: str,
    bot_generation: int,
    token_id: uuid.UUID | None = None,
) -> None:
    """Claim an opaque capability and execute its canonical command."""
    token_identity = {"token_id": token_id} if token_id is not None else {"raw_token": raw_token}
    claim = await claim_action_token(
        engine,
        **token_identity,
        chat_id=chat_id,
        telegram_user_id=telegram_user_id,
        claim_key=cq_id,
    )
    if claim.status == "invalid":
        await _answer(client, cq_id, "Действие устарело или доступ отозван")
        return
    if claim.status == "already_consumed":
        if claim.task_id is not None:
            await _answer(client, cq_id, f"Уже принято (задача #{claim.task_id})")
        else:
            await _answer(client, cq_id, "Действие уже завершено")
        return
    if claim.token_id is None or claim.target_id is None:
        await _answer(client, cq_id, "Некорректное действие")
        return

    if claim.action_kind == "ack_incident":
        await _handle_incident_ack_action(
            engine=engine,
            client=client,
            cq_id=cq_id,
            claim=claim,
            username=username,
            telegram_user_id=telegram_user_id,
            bot_generation=bot_generation,
        )
        return

    if claim.action_kind not in {"pause_ad", "activate_ad"} or claim.target_type != "fb_ad":
        await complete_action_token(
            engine,
            token_id=claim.token_id,
            failure_code="unsupported_action",
        )
        await _answer(client, cq_id, "Действие пока не поддерживается")
        return

    idempotency_key = _command_idempotency_key(claim)
    mutation_kind = claim.action_kind
    try:
        async with engine.begin() as conn:

            async def authorize_transaction(
                auth_conn,
                divergence_incident_key: str | None,
            ) -> None:
                identity = (
                    await auth_conn.execute(
                        text(
                            """
                            SELECT t.recipient_id, t.incident_id
                            FROM telegram_action_tokens t
                            WHERE t.id = :token_id
                            """
                        ),
                        {"token_id": claim.token_id},
                    )
                ).first()
                if identity is None:
                    raise TelegramActionAuthorityLost
                if identity.incident_id is not None:
                    await auth_conn.execute(
                        text("SELECT id FROM incidents WHERE id=:id FOR SHARE"),
                        {"id": identity.incident_id},
                    )

                if divergence_incident_key is not None:
                    await auth_conn.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": divergence_incident_key},
                    )
                    await auth_conn.execute(
                        text(
                            """
                            SELECT id FROM incidents
                            WHERE incident_key = :key
                            FOR UPDATE
                            """
                        ),
                        {"key": divergence_incident_key},
                    )

                await lock_owner_roster(auth_conn)
                owner_rows = (
                    await auth_conn.execute(
                        text(
                            """
                            SELECT id FROM telegram_recipients
                            WHERE role = 'owner'
                              AND revoked_at IS NULL
                              AND chat_id > 0
                            ORDER BY id
                            """
                        )
                    )
                ).all()
                recipient_id = uuid.UUID(str(identity.recipient_id))
                await serialize_recipient_delivery_state_in_transaction(
                    auth_conn,
                    [recipient_id, *(uuid.UUID(str(row.id)) for row in owner_rows)],
                )

                config_authorized = await auth_conn.scalar(
                    text(
                        """
                        SELECT 1
                        FROM telegram_config
                        WHERE singleton_key = 'default'
                          AND is_enabled
                          AND bot_token_encrypted <> ''
                          AND webhook_operation = 'configure'
                          AND webhook_state = 'configured'
                          AND webhook_applied_generation = webhook_generation
                          AND webhook_generation = :bot_generation
                        FOR SHARE
                        """
                    ),
                    {"bot_generation": int(bot_generation)},
                )
                if config_authorized is None:
                    raise TelegramActionAuthorityLost

                bound = (
                    await auth_conn.execute(
                        text(
                            """
                        UPDATE telegram_action_tokens
                        SET command_idempotency_key = COALESCE(
                            command_idempotency_key,
                            :idempotency_key
                        )
                        WHERE id = :token_id
                          AND claimed_at IS NOT NULL
                          AND claim_key = :claim_key
                          AND consumed_at IS NULL
                          AND revoked_at IS NULL
                          AND (
                              command_idempotency_key IS NULL
                              OR command_idempotency_key = :idempotency_key
                          )
                        RETURNING id
                        """
                        ),
                        {
                            "token_id": claim.token_id,
                            "claim_key": cq_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                ).first()
                if bound is None:
                    raise TelegramActionAuthorityLost
                await auth_conn.execute(
                    text(
                        """
                    SELECT s.ad_id
                    FROM telegram_action_tokens t
                    JOIN fb_ads a ON a.fb_ad_id = t.target_id
                    JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE t.id = :token_id
                      AND t.target_payload ? 'incident_key'
                    FOR SHARE OF s
                    """
                    ),
                    {"token_id": claim.token_id},
                )
                authority = (
                    await auth_conn.execute(
                        text(
                            """
                        SELECT 1
                        FROM telegram_action_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        LEFT JOIN incidents i ON i.id = t.incident_id
                        WHERE t.id = :token_id
                          AND t.claimed_at IS NOT NULL
                          AND t.claim_key = :claim_key
                          AND t.consumed_at IS NULL
                          AND t.revoked_at IS NULL
                          AND t.command_idempotency_key = :idempotency_key
                          AND (
                              (
                                  r.revoked_at IS NULL
                                  AND (
                                      t.required_role <> 'owner'
                                      OR r.role = 'owner'
                                  )
                                  AND (
                                      t.incident_id IS NULL
                                      OR (
                                          i.generation = t.incident_generation
                                          AND i.status IN
                                              ('open','acknowledged','executing')
                                      )
                                  )
                                  AND (
                                      NOT (t.target_payload ? 'incident_key')
                                      OR EXISTS (
                                          SELECT 1
                                          FROM fb_ads a
                                          JOIN ad_alert_state s ON s.ad_id = a.id
                                          WHERE a.fb_ad_id = t.target_id
                                            AND s.open_state_token IS NOT NULL
                                            AND s.open_state_token::text =
                                                t.target_payload->>'incident_key'
                                      )
                                  )
                              )
                              OR EXISTS (
                                  SELECT 1
                                  FROM command_idempotency_receipts receipt
                                  JOIN task_queue task ON task.id = receipt.task_id
                                  WHERE receipt.idempotency_key =
                                        t.command_idempotency_key
                                    AND receipt.action_kind = t.action_kind
                                    AND receipt.target_id = t.target_id
                                    AND task.task_type = 'meta_api_mutation'
                              )
                          )
                        FOR SHARE OF r
                        """
                        ),
                        {
                            "token_id": claim.token_id,
                            "claim_key": cq_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                ).first()
                if authority is None:
                    raise TelegramActionAuthorityLost

            receipt = await CommandService(engine).enqueue_ad_action(
                action_kind=mutation_kind,
                fb_ad_id=claim.target_id,
                idempotency_key=idempotency_key,
                requested_by=f"tg:{telegram_user_id}",
                correlation_id=claim.correlation_id,
                created_by_chat_id=chat_id,
                connection=conn,
                transaction_authorizer=authorize_transaction,
            )
            task_id = receipt.task_id
            completed = await complete_action_token(
                engine,
                token_id=claim.token_id,
                task_id=task_id,
                connection=conn,
            )
            if not completed:
                raise TelegramActionAuthorityLost
    except TelegramActionAuthorityLost:
        await _answer(client, cq_id, "Бот отключён или действие относится к старой версии")
        return
    except (CommandNotFoundError, CommandConflictError, ValueError) as exc:
        logger.warning(
            "opaque Telegram action rejected (kind=%s, reason=%s)",
            mutation_kind,
            type(exc).__name__,
        )
        await complete_action_token(
            engine,
            token_id=claim.token_id,
            failure_code="command_rejected",
        )
        await _answer(client, cq_id, "Действие больше недоступно")
        return
    except Exception:
        logger.warning(
            "opaque Telegram action is ambiguous before task attachment (kind=%s)",
            mutation_kind,
            exc_info=True,
        )
        # Keep the token claim retryable under the same callback query id. The
        # durable inbox will replay this update and CommandService reconciles by
        # idempotency key if the task commit actually succeeded.
        raise

    if mutation_kind == "pause_ad":
        try:
            from core.observer.writers import mark_alert_state_claimed

            await mark_alert_state_claimed(engine, fb_ad_id=claim.target_id)
        except Exception:
            logger.warning("failed to mark alert claimed after Telegram task", exc_info=True)
    label = "отключение" if mutation_kind == "pause_ad" else "включение"
    await _answer(client, cq_id, f"Задача на {label} принята (#{task_id})")


async def _handle_incident_ack_action(
    *,
    engine: AsyncEngine,
    client: TelegramUpdateClient,
    cq_id: str,
    claim: ActionTokenClaim,
    username: str,
    telegram_user_id: int,
    bot_generation: int,
) -> None:
    """Acknowledge the exact incident generation bound to a capability."""
    target_payload = claim.target_payload or {}
    try:
        target_incident_id = uuid.UUID(claim.target_id)
        payload_generation = int(target_payload["generation"])
    except (KeyError, TypeError, ValueError):
        target_incident_id = None
        payload_generation = None

    capability_is_valid = (
        claim.target_type == "incident"
        and target_incident_id is not None
        and claim.incident_id == target_incident_id
        and claim.incident_generation is not None
        and payload_generation == claim.incident_generation
    )
    if not capability_is_valid:
        await complete_action_token(
            engine,
            token_id=claim.token_id,
            failure_code="invalid_incident_action",
        )
        await _answer(client, cq_id, "Действие устарело или некорректно")
        return

    try:
        async with engine.begin() as conn:
            # Global notification lock order is incident -> recipient advisory
            # -> recipient row/delivery.  In particular, recipient revocation
            # takes the advisory lock before FOR UPDATE on the recipient row;
            # taking r FOR SHARE first here would form a deadlock cycle.
            locked_identity = (
                await conn.execute(
                    text(
                        """
                        SELECT t.recipient_id
                        FROM telegram_action_tokens t
                        JOIN incidents i ON i.id = t.incident_id
                        WHERE t.id = :token_id
                          AND i.id = :incident_id
                          AND i.generation = :incident_generation
                        FOR UPDATE OF i
                        """
                    ),
                    {
                        "token_id": claim.token_id,
                        "incident_id": target_incident_id,
                        "incident_generation": claim.incident_generation,
                    },
                )
            ).first()
            if locked_identity is None:
                raise TelegramActionAuthorityLost
            recipient_id = uuid.UUID(str(locked_identity.recipient_id))
            await lock_owner_roster(conn)
            owner_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id
                        FROM telegram_recipients
                        WHERE role = 'owner'
                          AND revoked_at IS NULL
                          AND chat_id > 0
                        ORDER BY id
                        """
                    )
                )
            ).all()
            await serialize_recipient_delivery_state_in_transaction(
                conn,
                [recipient_id, *(uuid.UUID(str(row.id)) for row in owner_rows)],
            )

            config_authorized = await conn.scalar(
                text(
                    """
                    SELECT 1 FROM telegram_config
                    WHERE singleton_key = 'default'
                      AND is_enabled
                      AND bot_token_encrypted <> ''
                      AND webhook_operation = 'configure'
                      AND webhook_state = 'configured'
                      AND webhook_applied_generation = webhook_generation
                      AND webhook_generation = :bot_generation
                    FOR SHARE
                    """
                ),
                {"bot_generation": int(bot_generation)},
            )
            if config_authorized is None:
                raise TelegramActionAuthorityLost

            # Recheck capability/recipient authority only after the config row
            # has been fenced.  Keeping it out of the config query makes actual
            # row-lock acquisition follow advisory -> config -> recipient.
            authority = (
                await conn.execute(
                    text(
                        """
                        SELECT (
                            i.acknowledged_at IS NOT NULL
                            AND i.acknowledged_by = :acknowledged_by
                        ) AS already_committed
                        FROM telegram_action_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        JOIN incidents i ON i.id = t.incident_id
                        WHERE t.id = :token_id
                          AND t.claimed_at IS NOT NULL
                          AND t.claim_key = :claim_key
                          AND t.consumed_at IS NULL
                          AND t.revoked_at IS NULL
                          AND t.action_kind = 'ack_incident'
                          AND t.incident_generation = :incident_generation
                          AND (
                              (
                                  r.revoked_at IS NULL
                                  AND (
                                      t.required_role <> 'owner'
                                      OR r.role = 'owner'
                                  )
                                  AND i.generation = t.incident_generation
                                  AND i.status IN ('open','acknowledged')
                              )
                              OR (
                                  i.acknowledged_at IS NOT NULL
                                  AND i.acknowledged_by = :acknowledged_by
                              )
                          )
                        FOR SHARE OF r
                        """
                    ),
                    {
                        "token_id": claim.token_id,
                        "claim_key": cq_id,
                        "incident_generation": claim.incident_generation,
                        "acknowledged_by": f"tg:{telegram_user_id}",
                    },
                )
            ).first()
            if authority is None:
                raise TelegramActionAuthorityLost
            if not bool(authority.already_committed):
                await acknowledge_incident(
                    engine,
                    incident_id=target_incident_id,
                    acknowledged_by=f"tg:{telegram_user_id}",
                    expected_generation=claim.incident_generation,
                    connection=conn,
                )
            consumed = await consume_action_token(
                engine,
                token_id=claim.token_id,
                connection=conn,
            )
            if not consumed:
                raise TelegramActionAuthorityLost
    except TelegramActionAuthorityLost:
        await _answer(client, cq_id, "Бот отключён или действие больше недоступно")
        return
    except (
        IncidentGenerationMismatchError,
        IncidentNotAcknowledgeableError,
        IncidentNotFoundError,
    ) as exc:
        logger.info(
            "opaque Telegram incident acknowledgement rejected (reason=%s)",
            type(exc).__name__,
        )
        await complete_action_token(
            engine,
            token_id=claim.token_id,
            failure_code="incident_unavailable",
        )
        await _answer(client, cq_id, "Инцидент уже изменился или закрыт")
        return
    except Exception:
        # The incident transition and its event are transactional.  Keeping the
        # capability claimed under the same callback id lets the durable inbox
        # safely retry an ambiguous response.
        logger.warning("Telegram incident acknowledgement is ambiguous", exc_info=True)
        raise

    await _answer(client, cq_id, "Инцидент принят")


async def _answer(client: TelegramUpdateClient, cq_id: str, text: str) -> None:
    """Acknowledge a durable callback update.

    The webhook worker may finalize the inbox row only after Telegram accepted
    the acknowledgement.  Propagating transport failures is safe: the opaque
    capability and ``CommandService`` idempotency key make a replay incapable
    of creating a second money task.
    """
    await client.answer_callback_query(cq_id, text=text)


__all__ = [
    "handle_action_callback",
]
