# -*- coding: utf-8 -*-
"""Восстановление секретов после wipe config-таблиц (напр. integration-тестами).

Читает data/secrets_backup_<timestamp>.json и вставляет обратно (ON CONFLICT DO UPDATE):
- vision_config.x_token_encrypted, profile_id
- telegram_config.bot_token_encrypted, chat_id, forum_*_thread_id

observer_config НЕ переносим — там не секреты (owner_tag, interval), а runtime
вынесен в Redis. Бэкап старого формата (legacy *_settings ключи) тоже читается —
см. fallback в main().

Запуск (после apply миграции 0001):
    python scripts/restore_secrets.py [path_to_backup.json]

Если path не указан — берётся самый свежий из data/secrets_backup_*.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_database_url() -> str:
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    for k in (
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        if os.environ.get(k):
            env_vars[k] = os.environ[k]

    db_url = env_vars.get("DATABASE_URL")
    if not db_url:
        host = env_vars.get("POSTGRES_HOST", "127.0.0.1")
        port = env_vars.get("POSTGRES_PORT", "5432")
        db_name = env_vars.get("POSTGRES_DB")
        user = env_vars.get("POSTGRES_USER")
        password = env_vars.get("POSTGRES_PASSWORD", "")
        if not (db_name and user):
            raise RuntimeError("Не нашёл POSTGRES_DB+POSTGRES_USER")
        from urllib.parse import quote_plus

        db_url = f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{db_name}"
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def _latest_backup() -> Path:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    backups = sorted(data_dir.glob("secrets_backup_*.json"), reverse=True)
    if not backups:
        raise RuntimeError("Нет бэкапов в data/secrets_backup_*.json")
    return backups[0]


async def _restore_vision(conn: Any, vision_rows: list[dict[str, Any]]) -> None:
    if not vision_rows:
        logger.warning("Нет vision-данных в backup — skip")
        return
    row = vision_rows[0]
    x_token_encrypted = row.get("x_token_encrypted")
    profile_id = row.get("profile_id") or ""

    if not x_token_encrypted:
        logger.warning("vision_settings.x_token_encrypted пустой — пропуск")
        return

    await conn.execute(
        text(
            """
            INSERT INTO vision_config
                (x_token_encrypted, profile_id)
            VALUES
                (:x_token, :profile_id)
            ON CONFLICT (singleton_key) DO UPDATE
                SET x_token_encrypted = EXCLUDED.x_token_encrypted,
                    profile_id = EXCLUDED.profile_id,
                    updated_at = NOW()
            """
        ),
        {
            "x_token": x_token_encrypted,
            "profile_id": profile_id,
        },
    )
    logger.info("vision_config: восстановлен (profile_id=%s)", profile_id)


async def _restore_telegram(conn: Any, telegram_rows: list[dict[str, Any]]) -> None:
    if not telegram_rows:
        logger.warning("Нет telegram-данных в backup — skip")
        return
    row = telegram_rows[0]
    bot_token_encrypted = row.get("bot_token_encrypted")

    def _int_or_none(v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    chat_id = _int_or_none(row.get("chat_id"))
    forum_warning_thread_id = _int_or_none(row.get("forum_warning_thread_id"))
    forum_stop_thread_id = _int_or_none(row.get("forum_stop_thread_id"))
    forum_enable_thread_id = _int_or_none(row.get("forum_enable_thread_id"))
    forum_ops_thread_id = _int_or_none(row.get("forum_ops_thread_id"))
    forum_digest_thread_id = _int_or_none(row.get("forum_digest_thread_id"))

    if not bot_token_encrypted:
        logger.warning("telegram_settings.bot_token_encrypted пустой — пропуск")
        return

    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (bot_token_encrypted, chat_id,
                 forum_warning_thread_id, forum_stop_thread_id,
                 forum_enable_thread_id, forum_ops_thread_id,
                 forum_digest_thread_id)
            VALUES
                (:tok, :cid, :fw, :fs, :fe, :fo, :fd)
            ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id,
                    forum_warning_thread_id = EXCLUDED.forum_warning_thread_id,
                    forum_stop_thread_id = EXCLUDED.forum_stop_thread_id,
                    forum_enable_thread_id = EXCLUDED.forum_enable_thread_id,
                    forum_ops_thread_id = EXCLUDED.forum_ops_thread_id,
                    forum_digest_thread_id = EXCLUDED.forum_digest_thread_id,
                    updated_at = NOW()
            """
        ),
        {
            "tok": bot_token_encrypted,
            "cid": chat_id,
            "fw": forum_warning_thread_id,
            "fs": forum_stop_thread_id,
            "fe": forum_enable_thread_id,
            "fo": forum_ops_thread_id,
            "fd": forum_digest_thread_id,
        },
    )
    logger.info("telegram_config: восстановлен (chat_id=%s)", chat_id)


async def main(backup_path_arg: str | None = None) -> int:
    backup_path = Path(backup_path_arg) if backup_path_arg else _latest_backup()
    logger.info("Restore from: %s", backup_path)

    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    tables = backup.get("tables", {})

    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        # Текущие имена (*_config); fallback на legacy *_settings ради старых бэкапов.
        await _restore_vision(
            conn, tables.get("vision_config") or tables.get("vision_settings", [])
        )
        await _restore_telegram(
            conn, tables.get("telegram_config") or tables.get("telegram_settings", [])
        )

    await engine.dispose()
    logger.info("Restore completed. Vision + Telegram токены на месте.")
    logger.info(
        "Остальные настройки (offer, interval, install_cost, ...) — введи через UI или API."
    )
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(main(arg)))
