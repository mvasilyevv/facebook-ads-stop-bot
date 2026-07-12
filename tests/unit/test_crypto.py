# -*- coding: utf-8 -*-
"""Unit-тесты core/crypto.py (H5): round-trip, fail-soft на чужом ключе, verify.

Money/security-критично: токены (Vision/TG/AdSetPro) хранятся зашифрованными.
Раньше модуль не имел тестов вообще — semantic-баг прошёл бы незаметно.
rotate_encryption_key использует raw SQL (нужна БД) — покрывается integration;
здесь покрыты его криптокомпоненты (Fernet round-trip) + verify + encrypt/decrypt.
"""

from __future__ import annotations

import multiprocessing
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


# --- C-1: fail-fast при пустом ключе + единый bootstrap ensure_encryption_key ---


# C-1(а): _get_fernet при пустом ENCRYPTION_KEY → явная ошибка, .env НЕ дописывается.
# Раньше воркер тихо генерировал свой ключ и дописывал в .env (гонка → порча токенов).
def test_get_fernet_missing_key_raises_and_does_not_write_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_fernet", None)  # сбрасываем кэш модуля
    # Пустой ключ в настройках.
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))
    fake_settings = SimpleNamespace(encryption_key="", encryption_key_verify="")
    monkeypatch.setattr("core.config.get_settings", lambda: fake_settings)

    env = tmp_path / ".env"
    env.write_text("SOME_OTHER=1\n", encoding="utf-8")
    before = env.read_text(encoding="utf-8")

    with pytest.raises(crypto.EncryptionKeyMissingError, match="ENCRYPTION_KEY не задан"):
        crypto._get_fernet()

    # .env не тронут — ключ не самогенерировался.
    assert env.read_text(encoding="utf-8") == before
    assert "ENCRYPTION_KEY" not in env.read_text(encoding="utf-8")


# C-1: encrypt/decrypt поверх _get_fernet тоже падают явной ошибкой при пустом ключе.
def test_encrypt_with_missing_key_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_fernet", None)
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))
    fake_settings = SimpleNamespace(encryption_key="", encryption_key_verify="")
    monkeypatch.setattr("core.config.get_settings", lambda: fake_settings)

    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt("secret")


# C-1(б): ensure_encryption_key идемпотентен — второй вызов не меняет ключ.
def test_ensure_encryption_key_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")

    key1 = crypto.ensure_encryption_key()
    assert key1  # ключ сгенерирован
    # Валидный Fernet-ключ.
    Fernet(key1.encode())
    assert env.read_text(encoding="utf-8").count("ENCRYPTION_KEY=") == 1

    key2 = crypto.ensure_encryption_key()
    assert key2 == key1  # второй вызов вернул тот же ключ
    # Ровно одна строка ENCRYPTION_KEY в .env — повторной записи не было.
    assert env.read_text(encoding="utf-8").count("ENCRYPTION_KEY=") == 1


# C-1: ensure_encryption_key не трогает уже заданный ключ (существующий побеждает).
def test_ensure_encryption_key_keeps_existing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))
    existing = Fernet.generate_key().decode()
    env = tmp_path / ".env"
    env.write_text(f"ENCRYPTION_KEY={existing}\n", encoding="utf-8")

    result = crypto.ensure_encryption_key()
    assert result == existing
    assert env.read_text(encoding="utf-8").count("ENCRYPTION_KEY=") == 1


# Хелпер для конкурентного теста: должен быть на модульном уровне (picklable для spawn).
def _worker_ensure_key(project_root: str) -> None:
    import core.crypto as _c

    _c._project_root = lambda: project_root  # type: ignore[assignment]
    _c.ensure_encryption_key()


# C-1(в): конкурентный вызов ensure_encryption_key из N процессов → ровно один ключ.
# Гонка была money-багом: без flock+double-check каждый процесс дописывал свой ключ.
def test_ensure_encryption_key_concurrent_single_key(monkeypatch, tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("BASE=1\n", encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_worker_ensure_key, args=(str(tmp_path),)) for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    content = env.read_text(encoding="utf-8")
    # Ровно одна строка ENCRYPTION_KEY несмотря на 8 параллельных процессов.
    assert content.count("ENCRYPTION_KEY=") == 1

    # И этот единственный ключ — валидный Fernet-ключ.
    key_line = next(ln for ln in content.splitlines() if ln.startswith("ENCRYPTION_KEY="))
    Fernet(key_line.split("=", 1)[1].encode())


# C-1: double-check внутри flock — если ключ появился, пока ждали lock, генерации нет.
# Симулируем: _read_env_key возвращает "" на быстром пути, затем реальный ключ под lock.
def test_ensure_encryption_key_double_check_skips_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))
    existing = Fernet.generate_key().decode()
    env = tmp_path / ".env"
    # На диске ключ УЖЕ есть (его записал «параллельный» процесс).
    env.write_text(f"ENCRYPTION_KEY={existing}\n", encoding="utf-8")

    calls = {"n": 0}
    real_read = crypto._read_env_key

    def fake_read(var: str = "ENCRYPTION_KEY") -> str:
        calls["n"] += 1
        # Первый (быстрый, до lock) вызов — как будто ключа ещё нет → идём в критсекцию.
        if calls["n"] == 1:
            return ""
        # Второй (double-check под lock) — ключ уже на диске.
        return real_read(var)

    monkeypatch.setattr(crypto, "_read_env_key", fake_read)

    result = crypto.ensure_encryption_key()
    assert result == existing
    # Ключ не перегенерирован — осталась одна исходная строка.
    assert env.read_text(encoding="utf-8").count("ENCRYPTION_KEY=") == 1
    assert calls["n"] >= 2  # был и быстрый путь, и double-check под lock


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
async def test_rotate_encryption_key_success_writes_all(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))

    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    fernet_old = Fernet(old_key.encode())

    tg_token = fernet_old.encrypt(b"tg-bot-token").decode()
    vis_token = fernet_old.encrypt(b"vision-x-token").decode()

    conn = _FakeConn(
        {
            "telegram_config": [(1, tg_token)],
            "vision_config": [(1, vis_token)],
            "adsetpro_credentials": [],
        }
    )
    _patch_fake_engine(monkeypatch, conn)

    rotated = await crypto.rotate_encryption_key(old_key, new_key)

    assert rotated == 2
    # UPDATE выполнился по обеим таблицам с расшифровываемыми полями.
    assert any("telegram_config" in u for u in conn.updates)
    assert any("vision_config" in u for u in conn.updates)


# Одно поле не расшифровывается старым ключом → EncryptionKeyRotationError ДО записи,
# ни один UPDATE не должен был уйти (частично перешифрованного состояния не возникает).
@pytest.mark.asyncio
async def test_rotate_encryption_key_partial_failure_writes_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))

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
async def test_rotate_encryption_key_error_message_lists_problem_fields(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(crypto, "_project_root", lambda: str(tmp_path))

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


# M-13 (аудит 2026-07-12): _read_env_key берёт ENCRYPTION_KEY из os.environ, если задан,
# даже когда .env-файла нет / он содержит другое значение. env-only деплой (k8s Secret).
def test_read_env_key_prefers_os_environ(monkeypatch, tmp_path) -> None:
    # .env-файл содержит СТАРЫЙ ключ, env-переменная — новый.
    env_file = tmp_path / ".env"
    env_file.write_text("ENCRYPTION_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setattr(crypto, "_env_path", lambda: str(env_file))
    monkeypatch.setenv("ENCRYPTION_KEY", "env-value")
    assert crypto._read_env_key() == "env-value"


# Без env-переменной падаем на .env-файл (прежнее bare-metal поведение сохранено).
def test_read_env_key_falls_back_to_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ENCRYPTION_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setattr(crypto, "_env_path", lambda: str(env_file))
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert crypto._read_env_key() == "file-value"


# Пустая env-переменная не перебивает файл (пустое = не задано).
def test_read_env_key_empty_env_falls_back(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ENCRYPTION_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setattr(crypto, "_env_path", lambda: str(env_file))
    monkeypatch.setenv("ENCRYPTION_KEY", "   ")
    assert crypto._read_env_key() == "file-value"
