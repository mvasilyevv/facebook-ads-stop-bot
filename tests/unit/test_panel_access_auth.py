from __future__ import annotations

import base64
import json

import fakeredis.aioredis
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core.auth import panel_access
from core.auth.panel_access import (
    PANEL_SESSION_COOKIE,
    PanelAuthError,
    TelegramSigningKeyNotFound,
    consume_oidc_attempt,
    consume_panel_ticket,
    create_oidc_authorization,
    create_panel_session,
    create_panel_ticket,
    delete_panel_session,
    load_panel_session,
    mark_owner_checked,
    safe_return_to,
    save_oidc_attempt,
    verify_telegram_id_token,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _signed_token(*, claims: dict, kid: str = "key-1") -> tuple[str, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    header = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    payload = _b64(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwks = {
        "keys": [
            {
                "kid": kid,
                "kty": "RSA",
                "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
                "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }
    return f"{header}.{payload}.{_b64(signature)}", jwks


def _claims(**overrides) -> dict:
    values = {
        "iss": "https://oauth.telegram.org",
        "aud": "12345",
        "id": 987654321,
        "nonce": "nonce-1",
        "iat": 1_700_000_000,
        "nbf": 1_700_000_000,
        "exp": 1_700_000_300,
    }
    values.update(overrides)
    return values


def test_cookie_and_open_redirect_contract() -> None:
    assert PANEL_SESSION_COOKIE == "__Secure-adpulse_panel_session_v1"
    assert safe_return_to("/campaigns?tab=active") == "/campaigns?tab=active"
    for unsafe in (
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "/auth/redeem?ticket=stolen",
        "campaigns",
    ):
        assert safe_return_to(unsafe) == "/"


@pytest.mark.asyncio
async def test_oidc_state_is_getdel_single_use_and_pkce_s256() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    state, url, attempt = create_oidc_authorization(
        client_id="12345",
        redirect_uri="https://app.adpulse.su/auth/telegram/callback",
        return_to="/settings",
    )
    assert "code_challenge_method=S256" in url
    assert f"state={state}" in url
    await save_oidc_attempt(redis, state, attempt, 600)
    assert await consume_oidc_attempt(redis, state) == attempt
    with pytest.raises(PanelAuthError, match="уже был использован"):
        await consume_oidc_attempt(redis, state)
    await redis.aclose()


@pytest.mark.asyncio
async def test_ticket_is_hashed_short_lived_and_single_use() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ticket, grant = await create_panel_ticket(
        redis,
        telegram_user_id=123456,
        source="telegram_oidc",
        return_to="/campaigns",
        ttl=60,
        now=1_700_000_000,
    )
    keys = await redis.keys("panel_auth:v1:ticket:*")
    assert len(keys) == 1 and ticket not in keys[0]
    assert await redis.ttl(keys[0]) <= 60
    assert await consume_panel_ticket(redis, ticket, now=1_700_000_010) == grant
    with pytest.raises(PanelAuthError, match="уже был использован"):
        await consume_panel_ticket(redis, ticket, now=1_700_000_011)
    await redis.aclose()


@pytest.mark.asyncio
async def test_session_is_hashed_and_logout_wins_role_refresh_race(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(panel_access.secrets, "token_urlsafe", lambda _size: "fixed-token")
    token, session = await create_panel_session(
        redis,
        telegram_user_id=123456,
        role="owner",
        source="telegram_oidc",
        ttl=43_200,
        now=1_700_000_000,
    )
    keys = await redis.keys("panel_auth:v1:session:*")
    assert len(keys) == 1 and token not in keys[0]
    assert await load_panel_session(redis, token, now=1_700_000_100) == session
    await delete_panel_session(redis, token)
    assert await mark_owner_checked(redis, token, session, 1_700_000_101) is None
    assert not await redis.keys("panel_auth:v1:session:*")
    await redis.aclose()


def test_rs256_claim_validation_and_unknown_kid() -> None:
    token, jwks = _signed_token(claims=_claims())
    verified = verify_telegram_id_token(
        token,
        jwks=jwks,
        client_id="12345",
        nonce="nonce-1",
        now=1_700_000_100,
    )
    assert verified["id"] == 987654321

    with pytest.raises(TelegramSigningKeyNotFound):
        verify_telegram_id_token(
            token,
            jwks={"keys": []},
            client_id="12345",
            nonce="nonce-1",
            now=1_700_000_100,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"iss": "https://evil.example"}, "издатель"),
        ({"aud": "other"}, "другого приложения"),
        ({"aud": ["12345", "other"], "azp": "other"}, "authorized party"),
        ({"nonce": "other"}, "nonce"),
        ({"exp": 1_699_999_000}, "истёк"),
        ({"iat": 1_700_001_000, "nbf": 1_700_001_000}, "ещё не действует"),
    ],
)
def test_rejects_invalid_security_claims(overrides: dict, message: str) -> None:
    token, jwks = _signed_token(claims=_claims(**overrides))
    with pytest.raises(PanelAuthError, match=message):
        verify_telegram_id_token(
            token,
            jwks=jwks,
            client_id="12345",
            nonce="nonce-1",
            now=1_700_000_100,
        )


def test_rejects_forged_signature() -> None:
    token, jwks = _signed_token(claims=_claims())
    forged = f"{token[:-2]}aa"
    with pytest.raises(PanelAuthError, match="Подпись"):
        verify_telegram_id_token(
            forged,
            jwks=jwks,
            client_id="12345",
            nonce="nonce-1",
            now=1_700_000_100,
        )
