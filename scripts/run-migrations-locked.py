#!/usr/bin/env python3
"""Advance one forward-only Alembic chain under a PostgreSQL advisory lock."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from migrations.baseline_contract import (
    BASELINE_RELATION_SENTINELS,
    CATALOG_ARTIFACTS_SQL,
    DATABASE_EXTENSION_LAYOUT_SQL,
    PUBLIC_APPLICATION_RELATIONS_SQL,
    PUBLIC_PARTITION_LAYOUT_SQL,
    PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL,
    assert_catalog_artifacts,
    describe_public_application_relations,
    describe_standalone_public_catalog_objects,
    validate_database_extension_layout,
    validate_public_partition_layout,
)
from migrations.revision_guard import (
    LinearRevisionChain,
    load_linear_revision_chain,
    validate_database_revisions,
)

LOCK_ID = 6_646_244_731_681_337_901
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def revision_chain(config: Config | None = None) -> LinearRevisionChain:
    selected = config or alembic_config()
    return load_linear_revision_chain(ScriptDirectory.from_config(selected))


async def acquire_lock(connection: AsyncConnection, lock_wait_seconds: float) -> None:
    deadline = time.monotonic() + lock_wait_seconds
    while time.monotonic() < deadline:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": LOCK_ID},
        )
        if acquired is True:
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"migration advisory lock was not acquired in {lock_wait_seconds:.0f}s")


async def validate_migration_target(
    connection: AsyncConnection,
    *,
    chain: LinearRevisionChain | None = None,
) -> str | None:
    """Accept only an empty target or a known ancestor on the linear chain.

    This preflight is read-only and runs while the advisory lock is held.  It
    deliberately has no stamp, drop or legacy conversion branch.
    """

    chain = chain or revision_chain()

    async def reject_standalone_catalog_objects(*, baseline_installed: bool) -> None:
        found = describe_standalone_public_catalog_objects(
            (await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))).mappings(),
            allow_manifested_routines=baseline_installed,
        )
        if found:
            raise ValueError(
                "fresh-install-only migration refused standalone public catalog "
                f"objects: {found!r}. Create a separate empty PostgreSQL database; "
                "extension-owned and dependency-owned objects are ignored, but no "
                "legacy application type or collation is accepted."
            )

    version_table = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
    revisions: list[str] = []
    if version_table is not None:
        rows = await connection.scalars(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        )
        revisions = [str(revision) for revision in rows]

    current_revision = validate_database_revisions(chain, revisions)
    if current_revision is not None:
        missing = [
            relation
            for relation in BASELINE_RELATION_SENTINELS
            if await connection.scalar(
                text("SELECT to_regclass(:relation)"),
                {"relation": relation},
            )
            is None
        ]
        if missing:
            raise ValueError(
                "versioned database is missing required baseline objects: "
                f"missing: {missing!r}; refusing a stamped or partial schema"
            )
        if current_revision == chain.head:
            assert_catalog_artifacts(
                (await connection.execute(text(CATALOG_ARTIFACTS_SQL))).mappings()
            )
            validate_database_extension_layout(
                (await connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL))).mappings(),
                baseline_installed=True,
            )
            await reject_standalone_catalog_objects(baseline_installed=True)
            validate_public_partition_layout(
                (await connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL))).mappings(),
                require_baseline_defaults=True,
            )
        return current_revision

    validate_database_extension_layout(
        (await connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL))).mappings(),
        baseline_installed=False,
    )
    found = describe_public_application_relations(
        (await connection.execute(text(PUBLIC_APPLICATION_RELATIONS_SQL))).mappings()
    )
    if found:
        raise ValueError(
            "fresh-install-only migration refused unversioned non-empty target; "
            f"public relations found: {found!r}. Create a separate empty PostgreSQL "
            "database; no legacy compatibility path exists."
        )
    await reject_standalone_catalog_objects(baseline_installed=False)
    validate_public_partition_layout(
        (await connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL))).mappings(),
        require_baseline_defaults=False,
    )
    return None


def _run_alembic_commands(connection, config: Config) -> None:
    """Run every Alembic phase on the session which owns the advisory lock."""

    marker = object()
    previous = config.attributes.get("connection", marker)
    config.attributes["connection"] = connection
    try:
        print("Running alembic upgrade head", flush=True)
        command.upgrade(config, "head")
        print("Running alembic current --check-heads", flush=True)
        command.current(config, check_heads=True)
        print("Running alembic check", flush=True)
        command.check(config)
    finally:
        if previous is marker:
            config.attributes.pop("connection", None)
        else:
            config.attributes["connection"] = previous


async def _release_lock(connection: AsyncConnection) -> None:
    if connection.closed or connection.invalidated:
        return
    if connection.in_transaction():
        await connection.rollback()
    released = await connection.scalar(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": LOCK_ID},
    )
    await connection.commit()
    if released is not True:
        raise RuntimeError("migration advisory lock ownership was lost")


async def run_locked_migrations(
    engine: AsyncEngine,
    *,
    config: Config | None = None,
    lock_wait_seconds: float = 180,
) -> int:
    selected_config = config or alembic_config()
    chain = revision_chain(selected_config)
    async with engine.connect() as connection:
        lock_acquired = False
        try:
            await acquire_lock(connection, lock_wait_seconds)
            lock_acquired = True
            print("Migration advisory lock acquired", flush=True)
            await validate_migration_target(connection, chain=chain)
            await connection.commit()
            await connection.run_sync(_run_alembic_commands, selected_config)
            current_revision = await validate_migration_target(connection, chain=chain)
            if current_revision != chain.head:
                raise RuntimeError(
                    f"database did not reach migration head {chain.head!r}; "
                    f"found {current_revision!r}"
                )
            print(f"Migration head {chain.head} and database object contract verified", flush=True)
            return 0
        finally:
            if lock_acquired:
                await _release_lock(connection)


async def run() -> int:
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
    try:
        return await run_locked_migrations(engine, lock_wait_seconds=timeout)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except (
        CommandError,
        ConnectionError,
        KeyError,
        RuntimeError,
        SQLAlchemyError,
        ValueError,
        TimeoutError,
    ) as exc:
        print(f"ERROR: migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
