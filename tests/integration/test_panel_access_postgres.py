from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import text

from core.auth.panel_access import (
    OidcAttempt,
    PanelAuthError,
    cleanup_expired_panel_auth_records,
    consume_oidc_attempt,
    consume_panel_ticket,
    create_panel_session,
    create_panel_ticket,
    delete_panel_session,
    load_panel_session,
    save_oidc_attempt,
)


@pytest.mark.asyncio
async def test_oidc_state_is_digest_only_and_exactly_one_concurrent_consumer_wins(
    pg_engine,
) -> None:
    state = "state-concurrent-" + "x" * 32
    attempt = OidcAttempt("nonce", "verifier", "/settings")
    await save_oidc_attempt(pg_engine, state, attempt, 600)

    async with pg_engine.connect() as conn:
        digest = await conn.scalar(
            text("SELECT state_digest FROM panel_oidc_attempts WHERE state_digest = :digest"),
            {"digest": hashlib.sha256(state.encode()).digest()},
        )
    assert digest == hashlib.sha256(state.encode()).digest()

    outcomes = await asyncio.gather(
        consume_oidc_attempt(pg_engine, state),
        consume_oidc_attempt(pg_engine, state),
        return_exceptions=True,
    )
    assert sum(result == attempt for result in outcomes) == 1
    failures = [result for result in outcomes if isinstance(result, PanelAuthError)]
    assert len(failures) == 1 and "уже был использован" in str(failures[0])


@pytest.mark.asyncio
async def test_ticket_and_session_are_digest_only_and_logout_wins_refresh(pg_engine) -> None:
    owner_id = 9_100_000_000 + uuid.uuid4().int % 100_000_000
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES (:owner_id, :owner_id, 'panel_session_test', 'owner')
                """
            ),
            {"owner_id": owner_id},
        )
    ticket, grant = await create_panel_ticket(
        pg_engine,
        telegram_user_id=owner_id,
        source="telegram_oidc",
        return_to="/campaigns",
        ttl=60,
    )
    expected_ticket_digest = hashlib.sha256(ticket.encode()).digest()
    async with pg_engine.connect() as conn:
        stored_ticket_digest = await conn.scalar(
            text("SELECT ticket_digest FROM panel_login_tickets WHERE ticket_digest = :digest"),
            {"digest": expected_ticket_digest},
        )
    assert stored_ticket_digest == expected_ticket_digest
    assert await consume_panel_ticket(pg_engine, ticket) == grant
    with pytest.raises(PanelAuthError, match="уже был использован"):
        await consume_panel_ticket(pg_engine, ticket)

    token, session = await create_panel_session(
        pg_engine,
        telegram_user_id=owner_id,
        role="owner",
        source="telegram_oidc",
        ttl=43_200,
    )
    assert await load_panel_session(pg_engine, token) == session
    await delete_panel_session(pg_engine, token)
    assert await load_panel_session(pg_engine, token) is None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE telegram_user_id = :owner_id"),
            {"owner_id": owner_id},
        )


@pytest.mark.asyncio
async def test_concurrent_session_issue_and_owner_revoke_never_authorizes(pg_engine) -> None:
    owner_id = 9_200_000_000 + uuid.uuid4().int % 100_000_000
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES (:owner_id, :owner_id, 'panel_session_race', 'owner')
                """
            ),
            {"owner_id": owner_id},
        )

    async def issue_session():
        return await create_panel_session(
            pg_engine,
            telegram_user_id=owner_id,
            role="owner",
            source="telegram_oidc",
            ttl=43_200,
        )

    async def revoke_owner() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET revoked_at = NOW() "
                    "WHERE telegram_user_id = :owner_id"
                ),
                {"owner_id": owner_id},
            )

    try:
        (token, _session), _ = await asyncio.gather(issue_session(), revoke_owner())
        assert await load_panel_session(pg_engine, token) is None
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE telegram_user_id = :owner_id"),
                {"owner_id": owner_id},
            )


@pytest.mark.asyncio
async def test_expired_auth_records_are_cleaned_in_bounded_batches(pg_engine) -> None:
    state = "expired-state-" + "y" * 32
    await save_oidc_attempt(
        pg_engine,
        state,
        OidcAttempt("nonce", "verifier", "/"),
        10,
        now=1_700_000_000,
    )
    await create_panel_ticket(
        pg_engine,
        telegram_user_id=123456,
        source="telegram_oidc",
        return_to="/",
        ttl=10,
        now=1_700_000_000,
    )
    token, _ = await create_panel_session(
        pg_engine,
        telegram_user_id=123456,
        role="owner",
        source="telegram_oidc",
        ttl=10,
        now=1_700_000_000,
    )

    deleted = await cleanup_expired_panel_auth_records(
        pg_engine,
        batch_size=10,
        now=1_700_000_100,
    )
    assert deleted == {
        "panel_oidc_attempts": 1,
        "panel_login_tickets": 1,
        "panel_sessions": 1,
    }
    assert await load_panel_session(pg_engine, token, now=1_700_000_100) is None
