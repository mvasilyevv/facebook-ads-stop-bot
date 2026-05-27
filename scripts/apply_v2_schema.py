# -*- coding: utf-8 -*-
"""Применить новую схему v2 к Postgres (полный wipe + create).

Алгоритм:
1. Проверить что есть DATABASE_URL.
2. DROP SCHEMA public CASCADE + CREATE SCHEMA public + GRANT.
3. CREATE EXTENSION IF NOT EXISTS pgcrypto (нужен для gen_random_uuid()).
4. Импортировать все ORM-классы (регистрация в Base.metadata).
5. Base.metadata.create_all() — создаёт 35 таблиц.
6. Для 7 партиционированных таблиц — создать партиции на текущий + следующий месяц.
7. Записать в system_config дефолтную retention_policy.

ВНИМАНИЕ: безвозвратно удаляет всю текущую БД. Запуск только после backup_secrets.py.

Использование:
    python scripts/apply_v2_schema.py [--confirm-drop]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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


# 8 партиционированных таблиц + столбец-партиция
_PARTITIONED_TABLES: list[tuple[str, str]] = [
    ("ad_metrics", "cycle_ts"),
    ("alert_events", "created_at"),
    ("scan_runs", "started_at"),
    ("meta_api_audit_log", "created_at"),
    ("meta_api_webhook_event", "received_at"),
    ("ad_library_snapshot", "scanned_at"),
    ("tracker_postback", "received_at"),
    ("adsetpro_postback_events", "received_at"),
]


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """Возвращает (from, to) для PARTITION OF — начало текущего и следующего месяца."""
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return f"{year:04d}-{month:02d}-01", f"{next_year:04d}-{next_month:02d}-01"


async def _drop_and_recreate_schema(engine) -> None:
    """DROP SCHEMA public CASCADE + CREATE SCHEMA public."""
    async with engine.begin() as conn:
        logger.info("DROP SCHEMA public CASCADE...")
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO PUBLIC"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    logger.info("Schema public пересоздана + pgcrypto enabled")


async def _create_all_tables(engine) -> None:
    """Импорт моделей и Base.metadata.create_all()."""
    # ВАЖНО: импорт здесь чтобы сначала упала проверка DATABASE_URL
    from core.models import Base  # noqa: PLC0415

    logger.info("Регистрация %d моделей...", len(Base.metadata.tables))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("All tables created.")


async def _create_first_partitions(engine) -> None:
    """Для каждой партиционированной таблицы создаёт партиции текущего + следующего месяца."""
    now = datetime.now(timezone.utc)
    current_from, current_to = _month_bounds(now.year, now.month)
    if now.month == 12:
        next_from, next_to = _month_bounds(now.year + 1, 1)
        if now.month + 1 > 12:
            next_next_from, next_next_to = _month_bounds(now.year + 1, 2)
        else:
            next_next_from, next_next_to = _month_bounds(now.year, now.month + 2)
    else:
        next_from, next_to = _month_bounds(now.year, now.month + 1)
        if now.month + 2 > 12:
            next_next_from, next_next_to = _month_bounds(now.year + 1, (now.month + 2) - 12)
        else:
            next_next_from, next_next_to = _month_bounds(now.year, now.month + 2)

    async with engine.begin() as conn:
        for table, _col in _PARTITIONED_TABLES:
            for year_month, fr, to in [
                (f"{now.year:04d}_{now.month:02d}", current_from, current_to),
                (
                    next_from.replace("-", "_")[:7],
                    next_from,
                    next_to,
                ),
            ]:
                part_name = f"{table}_{year_month}"
                stmt = (
                    f"CREATE TABLE IF NOT EXISTS {part_name} "
                    f"PARTITION OF {table} FOR VALUES FROM ('{fr}') TO ('{to}')"
                )
                logger.info("  %s [%s, %s)", part_name, fr, to)
                await conn.execute(text(stmt))
    logger.info("Партиции созданы (текущий + следующий месяц).")


async def _seed_retention_policy(engine) -> None:
    """Записать дефолтную retention_policy в system_config."""
    policy = {
        "ad_library_scan": "14 days",
        "ad_library_ad_orphan": "14 days",
        "ad_library_snapshot": "14 days",
        "ad_library_media_orphan": "immediate",
        "ad_metrics": "90 days",
        "alert_events": "365 days",
        "scan_runs": "30 days",
        "meta_api_audit_log": "30 days",
        "meta_api_webhook_event": "90 days",
        "tracker_postback": "60 days",
        "adsetpro_postback_events": "60 days",
        "task_queue_completed": "30 days",
        "task_queue_failed": "90 days",
        "enable_recommendations": "30 days",
        "telegram_invites_expired": "30 days",
        "cabinet_day_archives": "365 days",
        "ad_library_winner_archive": "forever",
        "ai_cache": "redis_ttl_only",
    }
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:key, CAST(:value AS JSONB), :desc)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {
                "key": "retention_policy",
                "value": json.dumps(policy),
                "desc": "Retention per table — см. DB_REDESIGN.md §4",
            },
        )
    logger.info("system_config.retention_policy записан.")


async def main(argv: list[str]) -> int:
    if "--confirm-drop" not in argv:
        logger.error(
            "Для безопасности требуется флаг --confirm-drop. "
            "Запуск без него отменяет работу. ВНИМАНИЕ: DROP всей схемы public безвозвратный."
        )
        return 1

    db_url = _get_database_url()
    logger.info("DATABASE_URL: %s", db_url.split("@")[-1])

    engine = create_async_engine(db_url, echo=False)

    try:
        await _drop_and_recreate_schema(engine)
        await _create_all_tables(engine)
        await _create_first_partitions(engine)
        await _seed_retention_policy(engine)
    finally:
        await engine.dispose()

    logger.info("=" * 60)
    logger.info("Schema v2 applied successfully.")
    logger.info("Следующий шаг: python scripts/restore_secrets.py")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
