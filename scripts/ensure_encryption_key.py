# -*- coding: utf-8 -*-
"""Единый bootstrap-шаг: гарантирует наличие ENCRYPTION_KEY в .env.

Money/security-критично. Раньше при пустом ключе каждый из 13+ параллельно
стартующих воркеров генерировал СВОЙ ключ и дописывал его в .env без блокировки
→ несколько строк ENCRYPTION_KEY, побеждала последняя → токены, зашифрованные
другими ключами, становились нерасшифровываемы (канал авто-стопа молча слепнул).

Теперь ключ генерируется РОВНО один раз здесь — под flock на .env.lock с
double-check внутри критической секции. run.sh зовёт этот скрипт ДО старта
воркеров. Идемпотентно: повторный запуск ничего не меняет.

Запуск:
    python scripts/ensure_encryption_key.py
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ensure_encryption_key")


def main() -> int:
    from core.crypto import ensure_encryption_key

    try:
        ensure_encryption_key()
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось гарантировать ENCRYPTION_KEY: %s", exc)
        return 1
    logger.info("ENCRYPTION_KEY готов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
