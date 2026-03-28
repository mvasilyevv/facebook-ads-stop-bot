# -*- coding: utf-8 -*-
"""Утилиты шифрования для хранения токенов в БД.

Использует Fernet (AES-128-CBC) из библиотеки cryptography.
Ключ берётся из ENCRYPTION_KEY в .env. Если ключ не задан —
автоматически генерируется и записывается в .env при первом запуске.
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Ленивая инициализация Fernet с ключом из env."""
    global _fernet
    if _fernet is not None:
        return _fernet

    from core.config import get_settings

    settings = get_settings()
    key = settings.encryption_key

    if not key:
        # Генерируем ключ и дописываем в .env
        key = Fernet.generate_key().decode()
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        try:
            with open(env_path, "a") as f:
                f.write(f"\nENCRYPTION_KEY={key}\n")
            logger.info("Сгенерирован ENCRYPTION_KEY и записан в .env")
        except OSError:
            logger.warning("Не удалось записать ENCRYPTION_KEY в .env")

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Шифрует строку, возвращает base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Расшифровывает строку."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("Не удалось расшифровать данные — неверный ключ или повреждённые данные")
        return ""
