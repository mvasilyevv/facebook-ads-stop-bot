# -*- coding: utf-8 -*-
"""Unit: H-6 — секретные поля Settings переведены на SecretStr.

Покрытие:
- (а) SecretStr-поля не светятся в repr/str настроек (защита от утечки в
  логи/Sentry/traceback при случайном print(settings) или f-string).
- (б) критичные потребители получают РЕАЛЬНОЕ значение через .get_secret_value()/
  reveal_secret(), а не строку "**********" (round-trip Fernet на ENCRYPTION_KEY —
  самый money-критичный потребитель, т.к. рвёт расшифровку токенов в БД).
- safe_url_for_log маскирует userinfo (H-6, п.3 — apps/api/main.py redis_url).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from core.config import Settings, reveal_secret, safe_url_for_log

_SECRET_FIELDS = [
    "postgres_password",
    "telegram_bot_token",
    "encryption_key",
    "encryption_key_verify",
    "api_key",
    "vision_x_token",
    "sentry_dsn",
    "tma_session_secret",
    "anthropic_api_key",
    "openai_api_key",
    "adsetpro_mcp_key",
    "adsetpro_postback_secret",
    "syntx_auth_token",
]


# (а) Ни один секрет не появляется в str(settings)/repr(settings) целиком.
def test_secret_fields_are_secretstr_type() -> None:
    overrides = {name: f"value-{name}" for name in _SECRET_FIELDS}
    settings = Settings(**overrides)
    for name in _SECRET_FIELDS:
        assert isinstance(getattr(settings, name), SecretStr), name


# (а) str(settings) и repr(settings) не содержат ни одного секретного значения открытым текстом.
def test_secret_values_not_leaked_in_repr_or_str() -> None:
    overrides = {name: f"leak-me-{name}-12345" for name in _SECRET_FIELDS}
    settings = Settings(**overrides)
    dumped = repr(settings) + str(settings)
    for name in _SECRET_FIELDS:
        secret_value = f"leak-me-{name}-12345"
        assert secret_value not in dumped, f"{name} утёк в repr/str"
    # Маска присутствует хотя бы для непустых полей.
    assert "**********" in dumped


# (а) SecretStr пустой строки не путается с "непустой маской" — bool() корректен.
def test_empty_secretstr_is_falsy() -> None:
    settings = Settings(api_key="")
    assert not settings.api_key
    assert bool(settings.api_key) is False


# (б) get_secret_value() отдаёт исходную строку без искажений.
def test_get_secret_value_roundtrip() -> None:
    settings = Settings(vision_x_token="tok_abc123XYZ")
    assert settings.vision_x_token.get_secret_value() == "tok_abc123XYZ"


# (б) Money-критичный потребитель: Fernet(ENCRYPTION_KEY) шифрует/расшифровывает
# round-trip. Если бы код забыл .get_secret_value() и передал str(SecretStr(...))
# в Fernet(), тот получил бы "**********" — либо упал бы (неверная длина ключа),
# либо тихо создал бы фернет с маской вместо реального ключа (расшифровка токенов
# в БД сломалась бы бесшумно). round-trip доказывает, что реальный ключ доехал.
def test_encryption_key_fernet_roundtrip_uses_real_value() -> None:
    real_key = Fernet.generate_key().decode()
    settings = Settings(encryption_key=real_key)

    fernet = Fernet(settings.encryption_key.get_secret_value().encode())
    plaintext = "vision-x-token-secret-value"
    encrypted = fernet.encrypt(plaintext.encode())
    assert fernet.decrypt(encrypted).decode() == plaintext

    # Если бы взяли str(SecretStr) по ошибке — получили бы маску, а не ключ,
    # и Fernet(...) на ней либо упал бы, либо дал другой (бесполезный) шифр.
    with pytest.raises(Exception):  # noqa: B017 - любой сбой Fernet на маске подтверждает тест
        Fernet(str(settings.encryption_key).encode())


# (б) reveal_secret унифицирует SecretStr и обычный str (тестовые SimpleNamespace-моки).
def test_reveal_secret_supports_both_secretstr_and_plain_str() -> None:
    assert reveal_secret(SecretStr("hidden")) == "hidden"
    assert reveal_secret("plain") == "plain"
    assert reveal_secret(SimpleNamespace()) != ""  # str(SimpleNamespace) не пуст, но не падает


# (б) reveal_secret на пустой SecretStr отдаёт "", не "**********".
def test_reveal_secret_empty_secretstr_returns_empty_string() -> None:
    assert reveal_secret(SecretStr("")) == ""


# safe_url_for_log маскирует userinfo (user:password@) — H-6 п.3.
def test_safe_url_for_log_strips_userinfo() -> None:
    masked = safe_url_for_log("redis://default:sup3rSecret@localhost:6380/0")
    assert "sup3rSecret" not in masked
    assert "default" not in masked
    assert "localhost:6380" in masked


# safe_url_for_log без userinfo — просто host:port/path, не падает.
def test_safe_url_for_log_no_userinfo() -> None:
    masked = safe_url_for_log("redis://localhost:6380/0")
    assert masked == "redis://localhost:6380/0"


# safe_url_for_log на мусорной строке не бросает исключение (используется в логах,
# не должен ронять процесс).
def test_safe_url_for_log_garbage_input_does_not_raise() -> None:
    assert safe_url_for_log("not a url at all") is not None
