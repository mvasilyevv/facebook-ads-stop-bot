# -*- coding: utf-8 -*-
"""Unit-тесты core/crypto.py (H5): round-trip, fail-soft на чужом ключе, verify.

Money/security-критично: токены (Vision/TG/AdSetPro) хранятся зашифрованными.
Раньше модуль не имел тестов вообще — semantic-баг прошёл бы незаметно.
rotate_encryption_key использует raw SQL (нужна БД) — покрывается integration;
здесь покрыты его криптокомпоненты (Fernet round-trip) + verify + encrypt/decrypt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


# Missing verification material must never start an unverified runtime.
def test_verify_key_empty_token_fails_closed() -> None:
    key = Fernet.generate_key().decode()
    with pytest.raises(crypto.EncryptionKeyMissingError, match="ENCRYPTION_KEY_VERIFY"):
        crypto.verify_encryption_key(key, "")


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


# --- Fail-closed runtime secret loading ---


# C-1(а): _get_fernet при пустом ENCRYPTION_KEY → явная ошибка, .env НЕ дописывается.
# Раньше воркер тихо генерировал свой ключ и дописывал в .env (гонка → порча токенов).
def test_get_fernet_missing_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(crypto, "_fernet", None)  # сбрасываем кэш модуля
    fake_settings = SimpleNamespace(encryption_key="", encryption_key_verify="")
    monkeypatch.setattr("core.config.get_settings", lambda: fake_settings)

    with pytest.raises(crypto.EncryptionKeyMissingError, match="ENCRYPTION_KEY не задан"):
        crypto._get_fernet()


# C-1: encrypt/decrypt поверх _get_fernet тоже падают явной ошибкой при пустом ключе.
def test_encrypt_with_missing_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(crypto, "_fernet", None)
    fake_settings = SimpleNamespace(encryption_key="", encryption_key_verify="")
    monkeypatch.setattr("core.config.get_settings", lambda: fake_settings)

    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt("secret")


def test_get_fernet_missing_verify_token_raises(monkeypatch) -> None:
    monkeypatch.setattr(crypto, "_fernet", None)
    fake_settings = SimpleNamespace(
        encryption_key=Fernet.generate_key().decode(),
        encryption_key_verify="",
    )
    monkeypatch.setattr("core.config.get_settings", lambda: fake_settings)

    with pytest.raises(crypto.EncryptionKeyMissingError, match="ENCRYPTION_KEY_VERIFY"):
        crypto._get_fernet()


# --- MID-18: rotate_encryption_key — атомарность (collect-all-then-write) ---


class _FakeConn:
    """Мок AsyncConnection: execute диспетчерится по подстроке SQL из заранее
    заданной карты `responses`, UPDATE'ы просто фиксируются в `updates`."""

    def __init__(self, responses: dict[str, list[tuple]]) -> None:
        self._responses = responses
        self.updates: list[str] = []

    async def execute(self, stmt, params=None):  # noqa: ANN001 - тестовый мок
        sql = str(stmt)
        result = MagicMock()
        if sql.strip().upper().startswith("SELECT"):
            for key, rows in self._responses.items():
                if key in sql:
                    result.all = MagicMock(return_value=rows)
                    return result
            result.all = MagicMock(return_value=[])
            return result
        # UPDATE — просто запоминаем, какую таблицу тронули.
        self.updates.append(sql)
        return result


class _FakeEngineCtx:
    """Async context manager, который `engine.begin()` обязан вернуть."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False  # исключение не глушим — как реальный AsyncEngine begin()


def _patch_fake_engine(monkeypatch, conn: _FakeConn) -> None:
    """Подменяет create_async_engine + get_settings().database_url на фейковые."""
    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_FakeEngineCtx(conn))
    fake_engine.dispose = AsyncMock()

    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda *a, **kw: fake_engine)
    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(database_url="postgresql+asyncpg://fake/fake"),
    )


# Все поля расшифровываются старым ключом → перешифровка проходит одной транзакцией,
# UPDATE выполняется для каждой затронутой таблицы (без частично перешифрованного состояния).
@pytest.mark.asyncio
async def test_rotate_encryption_key_success_writes_all(monkeypatch) -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    fernet_old = Fernet(old_key.encode())

    tg_token = fernet_old.encrypt(b"tg-bot-token").decode()
    vis_token = fernet_old.encrypt(b"vision-x-token").decode()
    vis_username = fernet_old.encrypt(b"vision-user").decode()

    conn = _FakeConn(
        {
            "telegram_config": [(1, tg_token)],
            "vision_config": [(1, vis_token, vis_username, None, None, None)],
            "adsetpro_credentials": [],
        }
    )
    _patch_fake_engine(monkeypatch, conn)

    rotated = await crypto.rotate_encryption_key(old_key, new_key)

    # Счётчик — строки, а не отдельные Fernet-поля: одна строка в каждой таблице.
    assert rotated == 2
    # UPDATE выполнился по обеим таблицам с расшифровываемыми полями.
    assert any("telegram_config" in u for u in conn.updates)
    assert any("vision_config" in u for u in conn.updates)


# Одно поле не расшифровывается старым ключом → EncryptionKeyRotationError ДО записи,
# ни один UPDATE не должен был уйти (частично перешифрованного состояния не возникает).
@pytest.mark.asyncio
async def test_rotate_encryption_key_partial_failure_writes_nothing(monkeypatch) -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    fernet_old = Fernet(old_key.encode())
    fernet_wrong = Fernet(Fernet.generate_key())  # чужой ключ — испортит одно поле

    tg_token_ok = fernet_old.encrypt(b"tg-bot-token").decode()
    vis_token_broken = fernet_wrong.encrypt(b"vision-x-token").decode()  # не расшифруется

    conn = _FakeConn(
        {
            "telegram_config": [(1, tg_token_ok)],
            "vision_config": [(2, vis_token_broken)],
            "adsetpro_credentials": [],
        }
    )
    _patch_fake_engine(monkeypatch, conn)

    with pytest.raises(crypto.EncryptionKeyRotationError) as exc_info:
        await crypto.rotate_encryption_key(old_key, new_key)

    # Список проблемных полей указывает ровно на сломанную запись.
    assert any("vision_config[2]" in p for p in exc_info.value.problems)
    # Ни один UPDATE не должен был уйти — даже валидное telegram_config-поле.
    assert conn.updates == []


# Список проблемных полей попадает в текст исключения (для алерта/лога вызывающей стороны).
@pytest.mark.asyncio
async def test_rotate_encryption_key_error_message_lists_problem_fields(monkeypatch) -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    fernet_wrong = Fernet(Fernet.generate_key())

    broken_token = fernet_wrong.encrypt(b"secret").decode()
    conn = _FakeConn(
        {
            "telegram_config": [(7, broken_token)],
            "vision_config": [],
            "adsetpro_credentials": [],
        }
    )
    _patch_fake_engine(monkeypatch, conn)

    with pytest.raises(crypto.EncryptionKeyRotationError, match=r"telegram_config\[7\]"):
        await crypto.rotate_encryption_key(old_key, new_key)
