from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from core.auth import desktop_access
from core.auth.desktop_access import (
    DESKTOP_SESSION_COOKIE,
    DesktopAccessError,
    build_desktop_launch_url,
    consume_desktop_ticket,
    create_desktop_session,
    create_desktop_ticket,
    delete_desktop_session,
    load_desktop_session,
)


def test_launch_url_is_fixed_to_https_application_origin():
    url = build_desktop_launch_url("https://app.adpulse.su/", "one-time-ticket")
    assert url == "https://app.adpulse.su/desktop-auth/redeem?ticket=one-time-ticket"
    with pytest.raises(DesktopAccessError, match="base URL"):
        build_desktop_launch_url("https://app.adpulse.su/path", "ticket")
    with pytest.raises(DesktopAccessError, match="base URL"):
        build_desktop_launch_url("http://app.adpulse.su", "ticket")


def test_v4_cookie_name_intentionally_does_not_accept_legacy_names():
    assert DESKTOP_SESSION_COOKIE == "__Secure-adpulse_desktop_session_v4"
    assert DESKTOP_SESSION_COOKIE != "adpulse_desktop_session"
    assert DESKTOP_SESSION_COOKIE != "__Secure-adpulse_desktop_session_v3"


@pytest.mark.asyncio
async def test_ticket_is_hashed_v4_host_bound_short_lived_and_single_use():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ticket, grant = await create_desktop_ticket(
        redis,
        telegram_user_id=123456,
        source="tma",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
        now=1_700_000_000,
    )
    keys = await redis.keys("desktop_access:v4:ticket:*")
    assert len(keys) == 1
    assert ticket not in keys[0]
    assert await redis.ttl(keys[0]) <= 300
    assert await consume_desktop_ticket(redis, ticket, now=1_700_000_010) == grant
    with pytest.raises(DesktopAccessError, match="уже использован"):
        await consume_desktop_ticket(redis, ticket, now=1_700_000_011)
    await redis.aclose()


@pytest.mark.asyncio
async def test_corrupted_and_expired_tickets_fail_closed():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ticket, _ = await create_desktop_ticket(
        redis,
        telegram_user_id=123456,
        source="panel",
        expected_hostname="desktop.adpulse.su",
        ttl=60,
        now=1_700_000_000,
    )
    key = (await redis.keys("desktop_access:v4:ticket:*"))[0]
    await redis.set(key, "not-json", ex=60)
    with pytest.raises(DesktopAccessError, match="Повреждён"):
        await consume_desktop_ticket(redis, ticket, now=1_700_000_001)

    ticket, _ = await create_desktop_ticket(
        redis,
        telegram_user_id=123456,
        source="panel",
        expected_hostname="desktop.adpulse.su",
        ttl=60,
        now=1_700_000_000,
    )
    with pytest.raises(DesktopAccessError, match="истёк"):
        await consume_desktop_ticket(redis, ticket, now=1_700_000_061)
    await redis.aclose()


@pytest.mark.asyncio
async def test_session_creation_uses_checked_set_nx(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(desktop_access.secrets, "token_urlsafe", lambda _: "fixed-token")
    _, session = await create_desktop_session(
        redis,
        telegram_user_id=123456,
        source="web",
        expected_hostname="desktop.adpulse.su",
        ttl=43_200,
        now=1_700_000_000,
    )
    keys = await redis.keys("desktop_access:v4:session:*")
    assert len(keys) == 1
    assert "fixed-token" not in keys[0]
    assert await load_desktop_session(redis, "fixed-token", now=1_700_000_100) == session
    with pytest.raises(DesktopAccessError, match="Не удалось создать"):
        await create_desktop_session(
            redis,
            telegram_user_id=123456,
            source="web",
            expected_hostname="desktop.adpulse.su",
            ttl=43_200,
            now=1_700_000_001,
        )
    await redis.aclose()


@pytest.mark.asyncio
async def test_logout_removes_desktop_session_without_resurrection():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    token, session = await create_desktop_session(
        redis,
        telegram_user_id=123456,
        source="tma",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
        now=1_700_000_000,
    )
    await delete_desktop_session(redis, token)
    assert session.telegram_user_id == 123456
    assert await load_desktop_session(redis, token, now=1_700_000_011) is None
    assert not await redis.keys("desktop_access:v4:session:*")
    await redis.aclose()


@pytest.mark.asyncio
async def test_malformed_session_is_deleted():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    token, _ = await create_desktop_session(
        redis,
        telegram_user_id=123456,
        source="web",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
        now=1_700_000_000,
    )
    key = (await redis.keys("desktop_access:v4:session:*"))[0]
    await redis.set(key, json.dumps({"telegram_user_id": 123456}), ex=300)
    assert await load_desktop_session(redis, token, now=1_700_000_001) is None
    assert await redis.get(key) is None
    await redis.aclose()
