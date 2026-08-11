#!/usr/bin/env python3
"""Read-only Alembic head and database object-contract verification."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys

from alembic import command
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_migration = importlib.import_module("scripts.run-migrations-locked")


def _run_alembic_checks(connection, config) -> None:
    marker = object()
    previous = config.attributes.get("connection", marker)
    config.attributes["connection"] = connection
    try:
        command.current(config, check_heads=True)
        command.check(config)
    finally:
        if previous is marker:
            config.attributes.pop("connection", None)
        else:
            config.attributes["connection"] = previous


async def check_database_contract() -> dict[str, str]:
    timeout = float(os.environ.get("MIGRATION_LOCK_TIMEOUT_SECONDS", "180"))
    database_url = URL.create(
        "postgresql+asyncpg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    ).render_as_string(hide_password=False)
    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"command_timeout": max(timeout, 30)},
    )
    config = _migration.alembic_config()
    chain = _migration.revision_chain(config)
    try:
        async with engine.connect() as connection:
            acquired = False
            try:
                await _migration.acquire_lock(connection, timeout)
                acquired = True
                current = await _migration.validate_migration_target(connection, chain=chain)
                if current != chain.head:
                    raise RuntimeError(
                        f"database is not at migration head; current={current!r}, head={chain.head!r}"
                    )
                await connection.commit()
                await connection.run_sync(_run_alembic_checks, config)
                confirmed = await _migration.validate_migration_target(connection, chain=chain)
                if confirmed != chain.head:
                    raise RuntimeError("database migration head changed during contract check")
                return {"current": current, "head": chain.head}
            finally:
                if acquired:
                    await _migration._release_lock(connection)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        result = asyncio.run(check_database_contract())
    except Exception:
        print("database contract check failed", file=sys.stderr)
        return 1
    print(json.dumps({"status": "READY", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
