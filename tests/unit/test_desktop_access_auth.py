from __future__ import annotations

import hashlib
import hmac
import json
from base64 import b64decode

import fakeredis.aioredis
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.auth.desktop_access import (
    DesktopAccessError,
    build_desktop_launch_url,
    build_guacamole_auth_data,
    build_guacamole_connect_url,
    consume_desktop_ticket,
    create_desktop_session,
    create_desktop_ticket,
    delete_desktop_session,
    load_desktop_session,
)


def _decrypt_guacamole_data(secret_hex: str, encoded: str) -> dict[str, object]:
    key = bytes.fromhex(secret_hex)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    padded = decryptor.update(b64decode(encoded)) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    signed = unpadder.update(padded) + unpadder.finalize()
    signature, plaintext = signed[:32], signed[32:]
    assert hmac.compare_digest(signature, hmac.new(key, plaintext, hashlib.sha256).digest())
    return json.loads(plaintext)


def test_launch_url_is_fixed_to_https_desktop_origin():
    url = build_desktop_launch_url("https://desktop.adpulse.su/", "one-time-ticket")
    assert url == "https://desktop.adpulse.su/desktop-auth/redeem?ticket=one-time-ticket"
    with pytest.raises(DesktopAccessError, match="base URL"):
        build_desktop_launch_url("https://desktop.adpulse.su/path", "ticket")
    with pytest.raises(DesktopAccessError, match="base URL"):
        build_desktop_launch_url("http://desktop.adpulse.su", "ticket")


def test_guacamole_json_auth_matches_official_wire_format():
    secret = "00112233445566778899aabbccddeeff"
    data = build_guacamole_auth_data(
        secret,
        telegram_user_id=123456,
        vnc_password="vnc-pass",
        ttl=60,
        now_ms=1_700_000_000_000,
    )
    payload = _decrypt_guacamole_data(secret, data)

    assert payload["username"] == "telegram:123456"
    assert payload["expires"] == 1_700_000_060_000
    connection = payload["connections"]["Vision Desktop"]
    assert connection["protocol"] == "vnc"
    assert connection["parameters"] == {
        "hostname": "127.0.0.1",
        "port": "5900",
        "password": "vnc-pass",
        "disable-display-resize": "true",
        "read-only": "false",
    }
    url = build_guacamole_connect_url(data)
    assert url.startswith("/guacamole/?data=")
    assert "vnc-pass" not in url


@pytest.mark.parametrize(
    ("secret", "password", "message"),
    [
        ("not-hex", "vnc-pass", "hex"),
        ("00" * 15, "vnc-pass", "32 hex"),
        ("00" * 16, "short", "8 ASCII"),
        ("00" * 16, "пароль12", "ASCII"),
        ("00" * 16, "bad\x00key", "8 ASCII"),
    ],
)
def test_guacamole_json_auth_rejects_unsafe_secrets(secret, password, message):
    with pytest.raises(DesktopAccessError, match=message):
        build_guacamole_auth_data(
            secret,
            telegram_user_id=123456,
            vnc_password=password,
            ttl=60,
        )


@pytest.mark.asyncio
async def test_ticket_is_hashed_short_lived_and_single_use():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ticket, grant = await create_desktop_ticket(
        redis,
        telegram_user_id=123456,
        source="tma",
        ttl=60,
        now=1_700_000_000,
    )
    keys = await redis.keys("desktop_auth:ticket:*")
    assert len(keys) == 1
    assert ticket not in keys[0]
    assert await redis.ttl(keys[0]) <= 60
    assert await consume_desktop_ticket(redis, ticket, now=1_700_000_010) == grant
    with pytest.raises(DesktopAccessError, match="уже использован"):
        await consume_desktop_ticket(redis, ticket, now=1_700_000_011)
    await redis.aclose()


@pytest.mark.asyncio
async def test_expired_ticket_is_rejected_even_if_redis_key_remains():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ticket, _ = await create_desktop_ticket(
        redis,
        telegram_user_id=123456,
        source="panel",
        ttl=60,
        now=1_700_000_000,
    )
    with pytest.raises(DesktopAccessError, match="истёк"):
        await consume_desktop_ticket(redis, ticket, now=1_700_000_061)
    await redis.aclose()


@pytest.mark.asyncio
async def test_desktop_session_is_server_side_and_revocable():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    token, session = await create_desktop_session(
        redis,
        telegram_user_id=123456,
        source="telegram_oidc",
        ttl=43_200,
        now=1_700_000_000,
    )
    keys = await redis.keys("desktop_auth:session:*")
    assert len(keys) == 1
    assert token not in keys[0]
    assert await load_desktop_session(redis, token, now=1_700_000_100) == session
    await delete_desktop_session(redis, token)
    assert await load_desktop_session(redis, token, now=1_700_000_101) is None
    await redis.aclose()
