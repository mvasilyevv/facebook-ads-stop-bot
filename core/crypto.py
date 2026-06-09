# -*- coding: utf-8 -*-
"""Утилиты шифрования для хранения токенов в БД.

Использует Fernet (AES-128-CBC) из библиотеки cryptography.
Ключ берётся из ENCRYPTION_KEY в .env. Если ключ не задан —
автоматически генерируется и записывается в .env при первом запуске.

Дополнительно:
- backup_encryption_key()   — сохраняет ключ в .encryption_key.backup (0600)
- verify_encryption_key()   — проверяет ключ по ENCRYPTION_KEY_VERIFY из .env
- rotate_encryption_key()   — перешифровывает все токены в БД при смене ключа
"""

from __future__ import annotations

import logging
import os
import stat

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

# Константа для верификации ключа
_VERIFY_PLAINTEXT = "encryption_key_verify_v1"


def _project_root() -> str:
    """Возвращает абсолютный путь к корню проекта."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_path() -> str:
    """Путь к файлу .env в корне проекта."""
    return os.path.join(_project_root(), ".env")


def backup_encryption_key(key: str) -> None:
    """Сохраняет ключ шифрования в резервный файл .encryption_key.backup.

    Файл создаётся с правами 0600 (только владелец).
    Вызывается автоматически при первой генерации ключа.

    Args:
        key: строка с Fernet-ключом (base64).
    """
    backup_path = os.path.join(_project_root(), ".encryption_key.backup")
    try:
        # Создаём файл с правами 0600 сразу при открытии
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
        # Явно выставляем права на случай umask
        os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("Резервная копия ENCRYPTION_KEY сохранена в %s", backup_path)
    except OSError as exc:
        logger.warning("Не удалось создать резервную копию ключа: %s", exc)


def _write_verify_token(key: str) -> None:
    """Шифрует эталонную строку текущим ключом и дописывает в .env.

    Args:
        key: строка с Fernet-ключом (base64).
    """
    if Fernet is None:
        return
    try:
        f = Fernet(key.encode() if isinstance(key, str) else key)
        verify_token = f.encrypt(_VERIFY_PLAINTEXT.encode()).decode()
        env = _env_path()
        with open(env, "a") as fp:
            fp.write(f"\nENCRYPTION_KEY_VERIFY={verify_token}\n")
        logger.info("ENCRYPTION_KEY_VERIFY записан в .env")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось записать ENCRYPTION_KEY_VERIFY в .env: %s", exc)


def verify_encryption_key(key: str, verify_token: str) -> None:
    """Проверяет, что ключ соответствует сохранённому verify-токену.

    Если расшифровка не совпадает с эталоном — логирует CRITICAL и бросает RuntimeError.
    При отсутствии verify_token (старые инсталляции) — пропускает проверку с предупреждением.

    Args:
        key: строка с Fernet-ключом (base64).
        verify_token: зашифрованный эталон из ENCRYPTION_KEY_VERIFY (.env).

    Raises:
        RuntimeError: если ключ не может расшифровать verify_token.
    """
    if Fernet is None:
        return

    if not verify_token:
        logger.warning(
            "ENCRYPTION_KEY_VERIFY не задан — проверка целостности ключа пропущена. "
            "Рекомендуется пересоздать ключ для получения верификационного токена."
        )
        return

    try:
        f = Fernet(key.encode() if isinstance(key, str) else key)
        decrypted = f.decrypt(verify_token.encode()).decode()
    except (InvalidToken, Exception) as exc:  # noqa: BLE001
        logger.critical(
            "КРИТИЧЕСКАЯ ОШИБКА: ENCRYPTION_KEY не совпадает с ENCRYPTION_KEY_VERIFY. "
            "Все зашифрованные токены в БД недоступны. Проверьте .encryption_key.backup. "
            "Причина: %s",
            exc,
        )
        raise RuntimeError(
            "ENCRYPTION_KEY не прошёл верификацию — ключ изменён или повреждён"
        ) from exc

    if decrypted != _VERIFY_PLAINTEXT:
        logger.critical(
            "КРИТИЧЕСКАЯ ОШИБКА: расшифрованное значение не совпадает с эталоном. "
            "ENCRYPTION_KEY скомпрометирован или заменён."
        )
        raise RuntimeError("ENCRYPTION_KEY вернул неверный plaintext при верификации")

    logger.debug("ENCRYPTION_KEY прошёл верификацию успешно")


async def rotate_encryption_key(old_key: str, new_key: str) -> int:
    """Перешифровывает все зашифрованные поля в БД при смене ключа.

    Сохраняет старый ключ в .encryption_key.old перед ротацией.
    Затронутые поля: telegram_config.bot_token_encrypted, vision_config.x_token_encrypted,
    adsetpro_credentials.api_key_encrypted/postback_secret_encrypted (BYTEA — N4).
    Использует raw SQL через AsyncEngine — без ORM-моделей, чтобы не зависеть от
    конкретной версии схемы.

    Args:
        old_key: текущий Fernet-ключ (base64).
        new_key: новый Fernet-ключ (base64).

    Returns:
        Количество перешифрованных записей.

    Raises:
        RuntimeError: если библиотека cryptography не установлена.
    """
    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    # Сохраняем старый ключ для возможного отката
    old_backup = os.path.join(_project_root(), ".encryption_key.old")
    try:
        fd = os.open(old_backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(old_key + "\n")
        os.chmod(old_backup, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("Старый ключ сохранён в %s для возможного отката", old_backup)
    except OSError as exc:
        logger.warning("Не удалось сохранить старый ключ в %s: %s", old_backup, exc)

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from core.config import get_settings

    db_url = get_settings().database_url
    engine = create_async_engine(db_url, echo=False)

    fernet_old = Fernet(old_key.encode() if isinstance(old_key, str) else old_key)
    fernet_new = Fernet(new_key.encode() if isinstance(new_key, str) else new_key)
    rotated = 0

    try:
        async with engine.begin() as conn:
            # telegram_config.bot_token_encrypted
            rows = (
                await conn.execute(text("SELECT id, bot_token_encrypted FROM telegram_config"))
            ).all()
            for row_id, encrypted in rows:
                if not encrypted:
                    continue
                try:
                    plaintext = fernet_old.decrypt(encrypted.encode()).decode()
                    new_blob = fernet_new.encrypt(plaintext.encode()).decode()
                    await conn.execute(
                        text(
                            "UPDATE telegram_config SET bot_token_encrypted = :b, "
                            "updated_at = NOW() WHERE id = :i"
                        ),
                        {"b": new_blob, "i": row_id},
                    )
                    rotated += 1
                    logger.info("telegram_config[%s]: bot_token_encrypted перешифрован", row_id)
                except InvalidToken:
                    logger.error(
                        "telegram_config[%s]: не расшифровать старым ключом — пропуск",
                        row_id,
                    )

            # vision_config.x_token_encrypted
            rows = (
                await conn.execute(text("SELECT id, x_token_encrypted FROM vision_config"))
            ).all()
            for row_id, encrypted in rows:
                if not encrypted:
                    continue
                try:
                    plaintext = fernet_old.decrypt(encrypted.encode()).decode()
                    new_blob = fernet_new.encrypt(plaintext.encode()).decode()
                    await conn.execute(
                        text(
                            "UPDATE vision_config SET x_token_encrypted = :b, "
                            "updated_at = NOW() WHERE id = :i"
                        ),
                        {"b": new_blob, "i": row_id},
                    )
                    rotated += 1
                    logger.info("vision_config[%s]: x_token_encrypted перешифрован", row_id)
                except InvalidToken:
                    logger.error(
                        "vision_config[%s]: не расшифровать старым ключом — пропуск",
                        row_id,
                    )

            # adsetpro_credentials.api_key_encrypted/postback_secret_encrypted (N4).
            # BYTEA (не TEXT): хранит Fernet-токен как utf-8 байты (core/adset_pro/
            # credentials.py). Без этого блока после ротации ключа AdSet.pro-credentials
            # не расшифровывались бы → депозиты не доезжают → лишние авто-стопы.
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, api_key_encrypted, postback_secret_encrypted "
                        "FROM adsetpro_credentials"
                    )
                )
            ).all()
            for row_id, api_enc, secret_enc in rows:
                updates: dict[str, bytes] = {}
                for col, enc in (
                    ("api_key_encrypted", api_enc),
                    ("postback_secret_encrypted", secret_enc),
                ):
                    if not enc:
                        continue
                    try:
                        # BYTEA → utf-8 строка Fernet-токена → decrypt старым → encrypt новым.
                        token = bytes(enc).decode("utf-8")
                        plaintext = fernet_old.decrypt(token.encode())
                        updates[col] = fernet_new.encrypt(plaintext)
                    except InvalidToken:
                        logger.error(
                            "adsetpro_credentials[%s].%s: не расшифровать старым ключом — пропуск",
                            row_id,
                            col,
                        )
                if updates:
                    set_sql = ", ".join(f"{c} = :{c}" for c in updates)
                    await conn.execute(
                        text(
                            f"UPDATE adsetpro_credentials SET {set_sql}, updated_at = NOW() "
                            "WHERE id = :i"
                        ),
                        {**updates, "i": row_id},
                    )
                    rotated += len(updates)
                    logger.info(
                        "adsetpro_credentials[%s]: перешифровано %d поле(й)",
                        row_id,
                        len(updates),
                    )
    finally:
        await engine.dispose()

    logger.info("Ротация ключа завершена: перешифровано %d записей", rotated)
    return rotated


def _get_fernet() -> Fernet:
    """Ленивая инициализация Fernet с ключом из env.

    При первом запуске без ключа — генерирует ключ, сохраняет в .env,
    создаёт резервную копию и записывает верификационный токен.
    При каждом запуске с ключом — верифицирует его по ENCRYPTION_KEY_VERIFY.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    from core.config import get_settings

    settings = get_settings()
    key = settings.encryption_key
    is_new_key = False

    if not key:
        # Генерируем ключ и дописываем в .env
        key = Fernet.generate_key().decode()
        env = _env_path()
        try:
            with open(env, "a") as f:
                f.write(f"\nENCRYPTION_KEY={key}\n")
            logger.info("Сгенерирован ENCRYPTION_KEY и записан в .env")
        except OSError:
            logger.warning("Не удалось записать ENCRYPTION_KEY в .env")
        is_new_key = True

    if is_new_key:
        # Бэкап и верификационный токен только при первой генерации
        backup_encryption_key(key)
        _write_verify_token(key)
    else:
        # Верифицируем существующий ключ
        verify_encryption_key(key, settings.encryption_key_verify)

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Шифрует строку, возвращает base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()  # type: ignore[no-any-return]


def decrypt(ciphertext: str) -> str:
    """Расшифровывает строку."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()  # type: ignore[no-any-return]
    except InvalidToken:
        logger.error("Не удалось расшифровать данные — неверный ключ или повреждённые данные")
        return ""
