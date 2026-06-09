# -*- coding: utf-8 -*-
"""Unit-тесты core/crypto.py (H5): round-trip, fail-soft на чужом ключе, verify.

Money/security-критично: токены (Vision/TG/AdSetPro) хранятся зашифрованными.
Раньше модуль не имел тестов вообще — semantic-баг прошёл бы незаметно.
rotate_encryption_key использует raw SQL (нужна БД) — покрывается integration;
здесь покрыты его криптокомпоненты (Fernet round-trip) + verify + encrypt/decrypt.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

import core.crypto as crypto


# encrypt→decrypt возвращает исходную строку (round-trip через подменённый _fernet)
def test_encrypt_decrypt_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    secret = "123456:ABC-DEF_bot_token_xyz"
    enc = crypto.encrypt(secret)
    assert enc != secret  # реально зашифровано, не plaintext
    assert crypto.decrypt(enc) == secret


# Пустой ввод не вызывает шифрование и возвращает "" (без обращения к _fernet)
def test_encrypt_decrypt_empty_returns_empty() -> None:
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


# decrypt чужим ключом → "" (InvalidToken проглочен, fail-soft), не падает
def test_decrypt_wrong_key_returns_empty(monkeypatch) -> None:
    enc = Fernet(Fernet.generate_key()).encrypt(b"secret").decode()
    # _fernet с ДРУГИМ ключом → расшифровать не сможет
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    assert crypto.decrypt(enc) == ""


# verify_encryption_key: правильный ключ + valid verify-токен → не бросает
def test_verify_key_happy() -> None:
    key = Fernet.generate_key().decode()
    token = Fernet(key.encode()).encrypt(crypto._VERIFY_PLAINTEXT.encode()).decode()
    crypto.verify_encryption_key(key, token)  # без исключения


# verify_encryption_key: ДРУГОЙ ключ (подмена) → RuntimeError (fail-fast)
def test_verify_key_mismatch_raises() -> None:
    key1 = Fernet.generate_key().decode()
    token = Fernet(key1.encode()).encrypt(crypto._VERIFY_PLAINTEXT.encode()).decode()
    key2 = Fernet.generate_key().decode()
    with pytest.raises(RuntimeError, match="верификацию"):
        crypto.verify_encryption_key(key2, token)


# verify_encryption_key: пустой verify-токен (старые инсталляции) → пропуск без исключения
def test_verify_key_empty_token_passes() -> None:
    key = Fernet.generate_key().decode()
    crypto.verify_encryption_key(key, "")  # warning, но не бросает


# verify_encryption_key: тот же ключ, но зашифрован НЕ эталонный plaintext → RuntimeError
def test_verify_key_wrong_plaintext_raises() -> None:
    key = Fernet.generate_key().decode()
    token = Fernet(key.encode()).encrypt(b"not_the_expected_plaintext").decode()
    with pytest.raises(RuntimeError, match="plaintext"):
        crypto.verify_encryption_key(key, token)


# N4: BYTEA-перешифровка adsetpro credentials при ротации ключа — round-trip без БД.
# Воспроизводит ровно операции блока adsetpro_credentials в rotate_encryption_key:
# BYTEA(utf-8 токен) → decode → decrypt старым → encrypt новым → читается новым ключом.
def test_adsetpro_bytea_reencrypt_roundtrip() -> None:
    from cryptography.fernet import InvalidToken

    fernet_old = Fernet(Fernet.generate_key())
    fernet_new = Fernet(Fernet.generate_key())
    api_key = "secret-mcp-key-123"

    # Как credentials.py хранит в BYTEA: encrypt(api_key).encode("utf-8") == fernet.encrypt(bytes).
    stored: bytes = fernet_old.encrypt(api_key.encode())

    # Логика rotate-блока:
    token = bytes(stored).decode("utf-8")
    plaintext = fernet_old.decrypt(token.encode())
    new_blob = fernet_new.encrypt(plaintext)

    # Новым ключом расшифровывается обратно в api_key; старым — InvalidToken.
    assert fernet_new.decrypt(new_blob).decode() == api_key
    with pytest.raises(InvalidToken):
        fernet_old.decrypt(new_blob)
