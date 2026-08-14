# -*- coding: utf-8 -*-
"""Fernet encryption for credentials stored in PostgreSQL.

Both ``ENCRYPTION_KEY`` and its independently persisted
``ENCRYPTION_KEY_VERIFY`` token are mandatory. Runtime code never creates,
repairs, backs up, or writes secret material.
"""

from __future__ import annotations

import logging

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

# Константа для верификации ключа
_VERIFY_PLAINTEXT = "encryption_key_verify_v1"


class EncryptionKeyMissingError(RuntimeError):
    """Required encryption material is absent from the environment."""


# Текст ошибки при пустом ключе — единый источник для _get_fernet и тестов.
_MISSING_KEY_MESSAGE = (
    "ENCRYPTION_KEY не задан. Provision ENCRYPTION_KEY and ENCRYPTION_KEY_VERIFY "
    "together before starting the application; runtime secret generation is forbidden."
)
_MISSING_VERIFY_MESSAGE = (
    "ENCRYPTION_KEY_VERIFY не задан. Provision the verification token together "
    "with ENCRYPTION_KEY; starting without key-integrity verification is forbidden."
)


def _reveal(value: object) -> str:
    """Возвращает строковое значение настройки: поддерживает str и pydantic SecretStr.

    Настройки секретов могут быть как `str`, так и `SecretStr` (миграция) — эта
    обёртка снимает зависимость модуля от конкретного типа поля.
    """
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return getter()
    return value if isinstance(value, str) else str(value)


def verify_encryption_key(key: str, verify_token: str) -> None:
    """Проверяет, что ключ соответствует сохранённому verify-токену.

    Missing or mismatched verification material fails closed.

    Args:
        key: строка с Fernet-ключом (base64).
        verify_token: зашифрованный эталон из ENCRYPTION_KEY_VERIFY (.env).

    Raises:
        RuntimeError: если ключ не может расшифровать verify_token.
    """
    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    if not verify_token:
        logger.critical(_MISSING_VERIFY_MESSAGE)
        raise EncryptionKeyMissingError(_MISSING_VERIFY_MESSAGE)

    try:
        f = Fernet(key.encode() if isinstance(key, str) else key)
        decrypted = f.decrypt(verify_token.encode()).decode()
    # LOW (аудит 02.07): (InvalidToken, Exception) было бессмысленной комбинацией —
    # InvalidToken это подкласс Exception, так что except перехватывал ровно то же
    # множество, что и голый except Exception, только выглядел как два разных случая.
    # Exception здесь осознан (не только InvalidToken): Fernet(...) на кривом base64-ключе
    # бросает ValueError/binascii.Error — тоже "ключ не прошёл верификацию".
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "КРИТИЧЕСКАЯ ОШИБКА: ENCRYPTION_KEY не совпадает с ENCRYPTION_KEY_VERIFY. "
            "Все зашифрованные токены в БД недоступны. "
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


class EncryptionKeyRotationError(RuntimeError):
    """Ротация ключа прервана: старым ключом не расшифровалось хотя бы одно поле.

    Бросается ДО каких-либо UPDATE — БД остаётся полностью в старом ключе
    (MID-18: раньше InvalidToken на отдельном поле просто пропускался, часть
    записей перешифровывалась, часть — нет, а .env уже мог быть переключён на
    новый ключ → частично перешифрованное состояние без способа его различить).
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        message = (
            "Ротация ENCRYPTION_KEY прервана: старым ключом не расшифровались поля: "
            + "; ".join(problems)
            + ". БД не изменена (ни одной записи не перешифровано), .env менять нельзя."
        )
        super().__init__(message)


async def rotate_encryption_key(old_key: str, new_key: str) -> int:
    """Перешифровывает все зашифрованные поля в БД при смене ключа.

    Затронутые поля: telegram_config.bot_token_encrypted, все Fernet-поля
    vision_config, adsetpro_credentials.api_key_encrypted/postback_secret_encrypted
    (BYTEA — N4).
    Использует raw SQL через AsyncEngine — без ORM-моделей, чтобы не зависеть от
    конкретной версии схемы.

    Атомарность (MID-18): двухфазный процесс в ОДНОЙ транзакции.
    1) Читаем ВСЕ строки, расшифровываем ВСЕ значения старым ключом. Если хотя бы
       одно значение не расшифровывается (InvalidToken) — собираем полный список
       проблемных полей и бросаем EncryptionKeyRotationError ДО единого UPDATE.
       Ни одна запись не меняется — БД остаётся согласованно на старом ключе.
    2) Только если фаза 1 полностью успешна — перешифровываем все значения новым
       ключом и пишем их в рамках одной транзакции (`engine.begin()`, COMMIT в конце
       блока или ROLLBACK при исключении). Частично перешифрованного состояния
       не может возникнуть: либо все UPDATE закоммитятся, либо ни один.

    Args:
        old_key: текущий Fernet-ключ (base64).
        new_key: новый Fernet-ключ (base64).

    Returns:
        Количество перешифрованных записей.

    Raises:
        RuntimeError: если библиотека cryptography не установлена.
        EncryptionKeyRotationError: если старым ключом не расшифровалось хотя бы
            одно поле — БД не изменена, вызывающий код НЕ должен переключать
            ENCRYPTION_KEY/ENCRYPTION_KEY_VERIFY на новую пару.
    """
    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from core.config import get_settings

    db_url = get_settings().database_url
    engine = create_async_engine(db_url, echo=False)

    fernet_old = Fernet(old_key.encode() if isinstance(old_key, str) else old_key)
    fernet_new = Fernet(new_key.encode() if isinstance(new_key, str) else new_key)

    try:
        async with engine.begin() as conn:
            # ── Фаза 1: собрать ВСЕ строки и расшифровать ВСЕ значения старым ключом.
            # Ничего не пишем — только читаем и валидируем. Копим проблемы, не
            # прерываемся на первой же ошибке, чтобы вернуть полный список сразу.
            problems: list[str] = []

            tg_rows = (
                await conn.execute(text("SELECT id, bot_token_encrypted FROM telegram_config"))
            ).all()
            tg_plain: dict[object, str] = {}
            for row_id, encrypted in tg_rows:
                if not encrypted:
                    continue
                try:
                    tg_plain[row_id] = fernet_old.decrypt(encrypted.encode()).decode()
                except InvalidToken:
                    problems.append(f"telegram_config[{row_id}].bot_token_encrypted")

            vision_columns = (
                "x_token_encrypted",
                "username_encrypted",
                "password_encrypted",
                "team_id_encrypted",
                "folder_id_encrypted",
            )
            vis_rows = (
                await conn.execute(
                    text(
                        "SELECT id, x_token_encrypted, username_encrypted, "
                        "password_encrypted, team_id_encrypted, folder_id_encrypted "
                        "FROM vision_config"
                    )
                )
            ).all()
            vis_plain: dict[object, dict[str, str]] = {}
            for row in vis_rows:
                row_id = row[0]
                decoded: dict[str, str] = {}
                for col, encrypted in zip(vision_columns, row[1:], strict=False):
                    if not encrypted:
                        continue
                    try:
                        decoded[col] = fernet_old.decrypt(encrypted.encode()).decode()
                    except InvalidToken:
                        problems.append(f"vision_config[{row_id}].{col}")
                if decoded:
                    vis_plain[row_id] = decoded

            # adsetpro_credentials.api_key_encrypted/postback_secret_encrypted (N4).
            # BYTEA (не TEXT): хранит Fernet-токен как utf-8 байты (core/adset_pro/
            # credentials.py).
            asp_rows = (
                await conn.execute(
                    text(
                        "SELECT id, api_key_encrypted, postback_secret_encrypted "
                        "FROM adsetpro_credentials"
                    )
                )
            ).all()
            asp_plain: dict[object, dict[str, bytes]] = {}
            for row_id, api_enc, secret_enc in asp_rows:
                decoded: dict[str, bytes] = {}
                for col, enc in (
                    ("api_key_encrypted", api_enc),
                    ("postback_secret_encrypted", secret_enc),
                ):
                    if not enc:
                        continue
                    try:
                        token = bytes(enc).decode("utf-8")
                        decoded[col] = fernet_old.decrypt(token.encode())
                    except InvalidToken:
                        problems.append(f"adsetpro_credentials[{row_id}].{col}")
                if decoded:
                    asp_plain[row_id] = decoded

            if problems:
                # Явная ошибка со списком проблемных полей, БЕЗ единой записи в БД.
                logger.critical(
                    "Ротация ENCRYPTION_KEY прервана на фазе расшифровки: %d проблемных "
                    "полей, БД не изменена: %s",
                    len(problems),
                    "; ".join(problems),
                )
                raise EncryptionKeyRotationError(problems)

            # ── Фаза 2: только если ВСЁ расшифровалось — перешифровываем новым ключом
            # и пишем в рамках той же транзакции. COMMIT — один раз, при выходе из
            # `async with engine.begin()`; при любом исключении — ROLLBACK целиком.
            rotated = 0

            for row_id, plaintext in tg_plain.items():
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

            for row_id, decoded in vis_plain.items():
                updates = {
                    col: fernet_new.encrypt(plaintext.encode()).decode()
                    for col, plaintext in decoded.items()
                }
                set_sql = ", ".join(f"{col} = :{col}" for col in updates)
                await conn.execute(
                    text(f"UPDATE vision_config SET {set_sql}, updated_at = NOW() WHERE id = :i"),
                    {**updates, "i": row_id},
                )
                rotated += len(updates)
                logger.info(
                    "vision_config[%s]: перешифровано %d поле(й)",
                    row_id,
                    len(updates),
                )

            for row_id, decoded in asp_plain.items():
                updates: dict[str, bytes] = {
                    col: fernet_new.encrypt(plaintext) for col, plaintext in decoded.items()
                }
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

    При пустом ключе или verify-токене бросает EncryptionKeyMissingError.
    Рантайм-генерация и запись секретов на диск отсутствуют.

    Raises:
        EncryptionKeyMissingError: если ENCRYPTION_KEY не задан.
        RuntimeError: если библиотека cryptography не установлена.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    from core.config import get_settings

    settings = get_settings()
    # Поле может быть как str, так и pydantic SecretStr (миграция секретов) — берём
    # значение единообразно через _reveal, чтобы модуль не зависел от типа настройки.
    key = _reveal(settings.encryption_key)

    if not key:
        # Fail-fast: не самогенерируем ключ в рантайме воркера (тихая порча токенов).
        logger.critical(_MISSING_KEY_MESSAGE)
        raise EncryptionKeyMissingError(_MISSING_KEY_MESSAGE)

    # Верифицируем существующий ключ по эталонному токену.
    verify_encryption_key(key, _reveal(settings.encryption_key_verify))

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def validate_encryption_material() -> None:
    """Fail unless the configured key and verification token form a valid pair."""
    _get_fernet()


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
