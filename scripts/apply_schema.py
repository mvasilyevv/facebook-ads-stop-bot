# -*- coding: utf-8 -*-
"""Destructive schema reset followed by the single fresh-install baseline.

Алгоритм:
1. Проверить что есть DATABASE_URL.
2. DROP SCHEMA public CASCADE + recreate PostgreSQL 16's safe public ACL.
3. Выполнить ``alembic upgrade head`` на пустой схеме.

Baseline владеет таблицами, constraints, functions, triggers, views и
DEFAULT-партициями.  Stamp/create_all и compatibility bootstrap запрещены.

ВНИМАНИЕ: безвозвратно удаляет всю схему. Использовать только для
явно disposable dev/test базы, никогда не для production/cutover/restore.

Использование:
    FB_AGENT_DISPOSABLE_DATABASE_URL=postgresql+asyncpg://... \
    FB_AGENT_ALLOW_DESTRUCTIVE_RESET=I_UNDERSTAND_THIS_DELETES_DATA \
      python scripts/apply_schema.py \
        --confirm-drop \
        --confirm-database fb_stop_bot_dev
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DATABASE_URL_ENV = "FB_AGENT_MIGRATION_DATABASE_URL"
DISPOSABLE_DATABASE_URL_ENV = "FB_AGENT_DISPOSABLE_DATABASE_URL"
DESTRUCTIVE_RESET_ENV = "FB_AGENT_ALLOW_DESTRUCTIVE_RESET"
DESTRUCTIVE_RESET_CONFIRMATION = "I_UNDERSTAND_THIS_DELETES_DATA"
_ALLOWED_DISPOSABLE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "postgres"})
_ALLOWED_DATABASE_SUFFIXES = ("_dev", "_test")


def _validated_database_url(value: str) -> str:
    """Return one explicit async PostgreSQL DSN for DROP and Alembic.

    ``apply_schema`` is deliberately destructive, so silently letting the
    subprocess rediscover a different database from ``.env`` is not safe.
    The dedicated override is consumed by ``migrations/env.py`` and is the
    exact string used by the engine that drops ``public``.
    """

    try:
        url = make_url(value)
    except Exception as exc:  # SQLAlchemy raises several URL parse errors.
        raise RuntimeError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("DATABASE_URL must use PostgreSQL")
    if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError("DATABASE_URL must use the asyncpg PostgreSQL driver")
    if not url.username or not url.host or not url.database:
        raise RuntimeError("DATABASE_URL must include user, host and database")
    normalized = url.set(
        drivername="postgresql+asyncpg",
        port=url.port or 5432,
    )
    return normalized.render_as_string(hide_password=False)


def _get_disposable_database_url() -> str:
    """Load only the dedicated destructive-reset DSN.

    Generic ``DATABASE_URL``, ``POSTGRES_*`` and ``.env`` are intentionally
    ignored: they are normal runtime inputs and therefore unsafe authority for
    ``DROP SCHEMA``.
    """

    value = os.environ.get(DISPOSABLE_DATABASE_URL_ENV, "").strip()
    if not value:
        raise RuntimeError(
            f"{DISPOSABLE_DATABASE_URL_ENV} is required; runtime DATABASE_URL is ignored"
        )
    return _validated_database_url(value)


def _validate_disposable_target(database_url: str, *, confirmed_database: str) -> str:
    """Fail closed unless every independent disposable-target proof matches."""

    if os.environ.get(DESTRUCTIVE_RESET_ENV) != DESTRUCTIVE_RESET_CONFIRMATION:
        raise RuntimeError(f"{DESTRUCTIVE_RESET_ENV} must equal {DESTRUCTIVE_RESET_CONFIRMATION}")

    url = make_url(_validated_database_url(database_url))
    database = str(url.database or "")
    host = str(url.host or "").lower()
    if host not in _ALLOWED_DISPOSABLE_HOSTS:
        raise RuntimeError(
            "destructive reset is allowed only on loopback or the local Compose postgres host"
        )
    if not database.endswith(_ALLOWED_DATABASE_SUFFIXES):
        raise RuntimeError("disposable database name must end with _dev or _test")
    if not confirmed_database or confirmed_database != database:
        raise RuntimeError("typed --confirm-database value does not match the DSN database")
    return url.render_as_string(hide_password=False)


async def _drop_and_recreate_schema(engine) -> None:
    """Recreate ``public`` with PostgreSQL 16's database-owner-only CREATE ACL."""
    async with engine.begin() as conn:
        logger.info("DROP SCHEMA public CASCADE...")
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public AUTHORIZATION pg_database_owner"))
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    logger.info("Schema public пересоздана; все DB-объекты создаст Alembic baseline")


async def _upgrade_head(database_url: str | None = None) -> int:
    """Run the same Alembic command used by release migrator containers."""

    environment = dict(os.environ)
    if database_url is not None:
        environment[MIGRATION_DATABASE_URL_ENV] = _validated_database_url(database_url)

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=PROJECT_ROOT,
        env=environment,
    )
    return await process.wait()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-drop",
        action="store_true",
        help="required explicit acknowledgement of DROP SCHEMA",
    )
    parser.add_argument(
        "--confirm-database",
        metavar="NAME",
        help="type the exact disposable database name from the dedicated DSN",
    )
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.confirm_drop or not args.confirm_database:
        logger.error(
            "Для безопасности требуются --confirm-drop и --confirm-database NAME. "
            "DROP всей схемы public безвозвратный."
        )
        return 1

    try:
        db_url = _validate_disposable_target(
            _get_disposable_database_url(),
            confirmed_database=args.confirm_database,
        )
    except RuntimeError as exc:
        logger.error("Destructive reset rejected: %s", exc)
        return 1
    logger.info("DATABASE_URL: %s", db_url.split("@")[-1])

    engine = create_async_engine(db_url, echo=False)

    try:
        await _drop_and_recreate_schema(engine)
    finally:
        await engine.dispose()

    migration_exit_code = await _upgrade_head(db_url)
    if migration_exit_code != 0:
        logger.error("alembic upgrade head failed with exit code %d", migration_exit_code)
        return migration_exit_code

    logger.info("=" * 60)
    logger.info("Schema applied successfully.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
