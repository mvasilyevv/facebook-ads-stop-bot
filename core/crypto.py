# -*- coding: utf-8 -*-
"""Утилиты шифрования для хранения токенов в БД.

Использует Fernet (AES-128-CBC) из библиотеки cryptography.
Ключ берётся из ENCRYPTION_KEY в .env. Ключ НЕ генерируется в рантайме воркера:
это делал каждый из 13+ параллельно стартующих процессов, дописывая свой ключ в
.env без блокировки → несколько строк ENCRYPTION_KEY, побеждала последняя →
токены, зашифрованные другими ключами, становились нерасшифровываемы (канал
авто-стопа молча слепнул). Теперь при пустом ключе _get_fernet бросает явную
ошибку (fail-fast), а генерация вынесена в единый bootstrap-шаг ensure_encryption_key()
под file-lock с double-check (его зовёт run.sh / scripts/ensure_encryption_key.py
ДО старта воркеров).

Дополнительно:
- ensure_encryption_key()   — единый bootstrap: генерирует ключ под flock, если его нет
- backup_encryption_key()   — сохраняет ключ в .encryption_key.backup (0600)
- verify_encryption_key()   — проверяет ключ по ENCRYPTION_KEY_VERIFY из .env
- rotate_encryption_key()   — перешифровывает все токены в БД при смене ключа
"""

from __future__ import annotations

import fcntl
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


class EncryptionKeyMissingError(RuntimeError):
    """ENCRYPTION_KEY не задан в окружении.

    Бросается вместо тихой рантайм-генерации ключа воркером. Сгенерировать ключ
    нужно единым bootstrap-шагом (run.sh / scripts/ensure_encryption_key.py).
    """


# Текст ошибки при пустом ключе — единый источник для _get_fernet и тестов.
_MISSING_KEY_MESSAGE = (
    "ENCRYPTION_KEY не задан — сгенерируйте его через scripts/ensure_encryption_key.py "
    "(его вызывает run.sh ДО старта воркеров). Рантайм-генерация ключа отключена: "
    "параллельные воркеры затирали .env разными ключами и портили зашифрованные токены."
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


def _project_root() -> str:
    """Возвращает абсолютный путь к корню проекта."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_path() -> str:
    """Путь к файлу .env в корне проекта."""
    return os.path.join(_project_root(), ".env")


def _lock_path() -> str:
    """Путь к lock-файлу для эксклюзивной генерации ключа."""
    return os.path.join(_project_root(), ".env.lock")


def _read_env_key(var: str = "ENCRYPTION_KEY") -> str:
    """Читает значение ENCRYPTION_KEY: сперва os.environ, затем .env-файл.

    M-13 (аудит 2026-07-12): раньше смотрели ТОЛЬКО файл .env, игнорируя реальную
    env-переменную. get_settings()/Fernet читают через pydantic-settings, где env
    имеет приоритет над .env-файлом. При env-only деплое (k8s/helm Secret → env, без
    материализации в файл) bootstrap не видел ключ → генерировал ВТОРОЙ → verify-fail
    краш / риск порчи зашифрованных токенов БД. Теперь os.environ — источник №1.

    Отдельно от get_settings(): та кэширует Settings на процесс, а нам нужен
    актуальный диск после double-check внутри flock. Побеждает последняя строка —
    паттерн `set -a; . ./.env` в run.sh: повторное присваивание перекрывает.

    Args:
        var: имя переменной окружения в .env.

    Returns:
        Значение переменной (без кавычек/пробелов) или "" если не найдено.
    """
    env_value = os.environ.get(var, "").strip()
    if env_value:
        return env_value

    env = _env_path()
    value = ""
    try:
        with open(env, encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, val = line.partition("=")
                if name.strip() != var:
                    continue
                val = val.strip()
                # Снимаем окружающие кавычки, если есть.
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                value = val
    except OSError:
        return ""
    return value


def ensure_encryption_key() -> str:
    """Единый bootstrap-шаг: гарантирует наличие ENCRYPTION_KEY в .env.

    Money/security-критично. Вызывается ОДИН раз до старта воркеров (run.sh →
    scripts/ensure_encryption_key.py). Идемпотентно: если ключ уже есть — ничего
    не меняет и возвращает его. Генерация — только под эксклюзивным flock на
    .env.lock, с double-check внутри критической секции (ключ мог появиться, пока
    ждали захвата), поэтому конкурентные вызовы дают ровно один ключ в .env.

    Returns:
        Актуальный ENCRYPTION_KEY (существующий или только что сгенерированный).

    Raises:
        RuntimeError: если библиотека cryptography не установлена.
    """
    if Fernet is None:
        raise RuntimeError("Библиотека cryptography не установлена")

    # Быстрый путь без блокировки: ключ уже есть.
    existing = _read_env_key()
    if existing:
        return existing

    lock_path = _lock_path()
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        # Эксклюзивная блокировка на межпроцессном уровне (flock переживает fork,
        # снимается при close/exit процесса-владельца).
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Double-check: пока ждали lock, другой процесс мог записать ключ.
        existing = _read_env_key()
        if existing:
            logger.info("ENCRYPTION_KEY уже присутствует в .env (записан параллельным процессом)")
            return existing

        key = Fernet.generate_key().decode()
        env = _env_path()
        with open(env, "a", encoding="utf-8") as f:
            f.write(f"\nENCRYPTION_KEY={key}\n")
        logger.info("Сгенерирован ENCRYPTION_KEY и записан в .env")

        # Бэкап и верификационный токен — только при первой генерации.
        backup_encryption_key(key)
        _write_verify_token(key)
        return key
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


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
    # LOW (аудит 02.07): (InvalidToken, Exception) было бессмысленной комбинацией —
    # InvalidToken это подкласс Exception, так что except перехватывал ровно то же
    # множество, что и голый except Exception, только выглядел как два разных случая.
    # Exception здесь осознан (не только InvalidToken): Fernet(...) на кривом base64-ключе
    # бросает ValueError/binascii.Error — тоже "ключ не прошёл верификацию".
    except Exception as exc:  # noqa: BLE001
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

    Сохраняет старый ключ в .encryption_key.old перед ротацией.
    Затронутые поля: telegram_config.bot_token_encrypted, vision_config.x_token_encrypted,
    adsetpro_credentials.api_key_encrypted/postback_secret_encrypted (BYTEA — N4).
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
            ENCRYPTION_KEY в .env на new_key.
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

            vis_rows = (
                await conn.execute(text("SELECT id, x_token_encrypted FROM vision_config"))
            ).all()
            vis_plain: dict[object, str] = {}
            for row_id, encrypted in vis_rows:
                if not encrypted:
                    continue
                try:
                    vis_plain[row_id] = fernet_old.decrypt(encrypted.encode()).decode()
                except InvalidToken:
                    problems.append(f"vision_config[{row_id}].x_token_encrypted")

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

            for row_id, plaintext in vis_plain.items():
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

    При пустом ключе — бросает EncryptionKeyMissingError (fail-fast). Рантайм-
    генерация отключена намеренно: 13+ параллельных воркеров дописывали в .env
    каждый свой ключ без блокировки → тихая порча зашифрованных токенов. Ключ
    генерируется единым bootstrap-шагом (ensure_encryption_key через run.sh).
    При каждом запуске с ключом — верифицирует его по ENCRYPTION_KEY_VERIFY.

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
