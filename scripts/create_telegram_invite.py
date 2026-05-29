# -*- coding: utf-8 -*-
"""Создать invite-код для подключения к Telegram-боту (новая схема).

После полного wipe БД нужно заново подключить пользователей.
Этот скрипт создаёт активный invite-код на 7 дней.

Запуск:
    python scripts/create_telegram_invite.py [--role owner|recipient] [--ttl-days N]

Затем в TG пиши боту: /start <код>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _get_database_url() -> str:
    """Локальная копия helper'а (см. scripts/backup_secrets.py)."""
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


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _generate_code(length: int = 16) -> str:
    """URL-safe alphanumeric. 16 символов = ~95 bit энтропии."""
    return secrets.token_urlsafe(12)[:length].upper().replace("-", "X").replace("_", "Y")


async def main(role: str, ttl_days: int) -> int:
    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)

    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_invites
                        (code, created_by, expires_at)
                    VALUES
                        (:code, :by, :exp)
                    """
                ),
                {
                    "code": code,
                    "by": f"cli:role={role}",
                    "exp": expires_at,
                },
            )
    finally:
        await engine.dispose()

    print("=" * 60)
    print(f"Invite-код:  {code}")
    print(f"Действует до: {expires_at.isoformat()}")
    print(f"Назначение:   role={role}")
    print()
    print("Использование в TG (в личке с ботом):")
    print(f"    /start {code}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="owner", choices=("owner", "recipient"))
    parser.add_argument("--ttl-days", type=int, default=7)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.role, args.ttl_days)))
