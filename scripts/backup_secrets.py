# -*- coding: utf-8 -*-
"""Бэкап критичных секретов из legacy-схемы перед DROP.

Зачем: после wipe БД нужно восстановить Vision X-Token и Telegram bot token,
не вводя их руками. Они зашифрованы Fernet с ключом из .env (ENCRYPTION_KEY) —
ключ останется тот же, поэтому encrypted blob'ы можно вставить как есть в новые
таблицы vision_config / telegram_config.

Стратегия:
1. Читаем сырые rows из vision_settings, telegram_settings, observer_settings.
2. Сохраняем в data/secrets_backup_<timestamp>.json (НЕ в git!).
3. restore_secrets.py (после apply миграции 0001) вставляет в новые таблицы.

ENCRYPTION_KEY сам по себе НЕ бэкапится — он живёт в .env, его не трогаем.
Если ключ потеряется — blob'ы станут бесполезны (но это уже забота .env-бэкапа).

Запуск:
    python scripts/backup_secrets.py

Output: data/secrets_backup_YYYYMMDD_HHMMSS.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_database_url() -> str:
    """Берём DATABASE_URL либо собираем из POSTGRES_* (env + .env)."""
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    # env vars приоритетнее
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
            raise RuntimeError("Не нашёл ни DATABASE_URL, ни POSTGRES_DB+POSTGRES_USER в env/.env")
        from urllib.parse import quote_plus

        db_url = f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{db_name}"
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


async def _dump_table(conn: Any, table_name: str) -> list[dict[str, Any]]:
    """Сырое чтение всех строк таблицы как list[dict]."""
    try:
        result = await conn.execute(text(f"SELECT * FROM {table_name}"))
    except Exception as exc:
        logger.warning("Не могу прочитать %s: %s", table_name, exc)
        return []

    rows: list[dict[str, Any]] = []
    for row in result.mappings():
        row_dict: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                row_dict[key] = value.isoformat()
            elif isinstance(value, Decimal):
                row_dict[key] = str(value)  # точное представление
            elif hasattr(value, "hex") and not isinstance(value, (bytes, str)):
                row_dict[key] = str(value)
            else:
                row_dict[key] = value
        rows.append(row_dict)
    return rows


async def main() -> int:
    db_url = _get_database_url()
    logger.info("Connecting to DB...")
    engine = create_async_engine(db_url, echo=False)

    backup: dict[str, Any] = {
        "backup_at": datetime.now(timezone.utc).isoformat(),
        "source_schema_version": "legacy",
        "warning": "Содержит encrypted blob'ы. ENCRYPTION_KEY из .env обязателен для расшифровки.",
        "tables": {},
    }

    tables_to_backup = [
        "vision_settings",
        "telegram_settings",
        "observer_settings",
    ]

    async with engine.connect() as conn:
        for table in tables_to_backup:
            rows = await _dump_table(conn, table)
            backup["tables"][table] = rows
            logger.info("  %s: %d row(s)", table, len(rows))

    await engine.dispose()

    data_dir = Path(__file__).resolve().parent.parent / "data"  # noqa: ASYNC240
    data_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = data_dir / f"secrets_backup_{timestamp}.json"

    out_path.write_text(
        json.dumps(backup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 0600 — чтобы только владелец читал (encrypted, но всё равно паранойя)
    os.chmod(out_path, 0o600)

    logger.info("Backup saved: %s", out_path)
    logger.info(
        "Размер: %.1f KB. Файл НЕ коммитить в git (см. .gitignore).",
        out_path.stat().st_size / 1024,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
