from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core.auth.panel_access import (
    PANEL_SESSION_COOKIE,
    PanelAuthError,
    TelegramSigningKeyNotFound,
    create_oidc_authorization,
    safe_return_to,
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


def test_oidc_authorization_uses_pkce_s256() -> None:
    state, url, attempt = create_oidc_authorization(
        client_id="12345",
        redirect_uri="https://app.adpulse.su/auth/telegram/callback",
        return_to="/settings",
    )
    assert "code_challenge_method=S256" in url
    assert f"state={state}" in url
    assert attempt.return_to == "/settings"
    assert attempt.nonce and attempt.code_verifier


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
    # Портим ПЕРВЫЙ символ подписи, а не последний. В последнем символе base64url
    # значимы лишь два бита, остальные — добивка: примерно каждая четвёртая
    # «подделка» декодировалась в те же байты, подпись сходилась, и тест падал
    # с «DID NOT RAISE» на ровном месте. Первый символ значим целиком.
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{'B' if signature[0] != 'B' else 'C'}{signature[1:]}"
    with pytest.raises(PanelAuthError, match="Подпись"):
        verify_telegram_id_token(
            forged,
            jwks=jwks,
            client_id="12345",
            nonce="nonce-1",
            now=1_700_000_100,
        )
