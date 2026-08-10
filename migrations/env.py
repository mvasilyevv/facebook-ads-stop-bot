# -*- coding: utf-8 -*-
"""Alembic env.py для async-миграций."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

# Импорт всего пакета core.models регистрирует current ORM metadata.
from core.config import get_settings
from core.models import Base  # noqa: F401
from migrations.baseline_contract import (
    BASELINE_RELATION_SENTINELS,
    BASELINE_REVISION,
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

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL берём из настроек приложения (core.config), а не из захардкоженного
# alembic.ini — иначе в Docker/на сервере alembic идёт на localhost:5433 вместо
# хоста из POSTGRES_HOST (env), хотя apply_schema (тоже core.config) идёт верно.


def _migration_database_url() -> str:
    forced = os.environ.get("FB_AGENT_MIGRATION_DATABASE_URL")
    value = forced or get_settings().database_url
    try:
        url = make_url(value)
    except Exception as exc:
        raise RuntimeError("migration database URL is invalid") from exc
    if url.get_backend_name() != "postgresql" or url.drivername not in {
        "postgresql",
        "postgresql+asyncpg",
    }:
        raise RuntimeError("migrations require a PostgreSQL asyncpg URL")
    if not url.username or not url.host or not url.database:
        raise RuntimeError("migration database URL must include user, host and database")
    return url.set(drivername="postgresql+asyncpg", port=url.port or 5432).render_as_string(
        hide_password=False
    )


# ConfigParser treats percent characters as interpolation. Escaping here keeps
# percent-encoded passwords byte-for-byte identical when Alembic reads it back.
config.set_main_option("sqlalchemy.url", _migration_database_url().replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError(
        "offline migration is unsupported: the fresh-install baseline must prove "
        "that its PostgreSQL target is empty before executing DDL"
    )


def _validate_fresh_install_target(connection) -> frozenset[str]:
    """Reject legacy, unversioned non-empty and falsely stamped databases."""

    def reject_standalone_catalog_objects(*, baseline_installed: bool) -> None:
        found = describe_standalone_public_catalog_objects(
            connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL)).mappings(),
            allow_manifested_routines=baseline_installed,
        )
        if found:
            raise RuntimeError(
                "fresh-install-only migration refused standalone public catalog "
                f"objects: {found!r}. Create a separate empty PostgreSQL database; "
                "extension-owned and dependency-owned objects are ignored, but no "
                "legacy application type or collation is accepted."
            )

    version_table = connection.execute(
        text("SELECT to_regclass('public.alembic_version')")
    ).scalar_one_or_none()
    revisions: list[str] = []
    if version_table is not None:
        revisions = list(
            connection.execute(
                text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
            ).scalars()
        )

    if revisions:
        if revisions != [BASELINE_REVISION]:
            raise RuntimeError(
                "fresh-install-only migration refused historical target; "
                f"expected revision {BASELINE_REVISION!r}, found {revisions!r}. "
                "Create a separate empty PostgreSQL database; no stamp, upgrade, "
                "drop or legacy conversion path exists."
            )
        missing = [
            relation
            for relation in BASELINE_RELATION_SENTINELS
            if connection.execute(
                text("SELECT to_regclass(:relation)"), {"relation": relation}
            ).scalar_one_or_none()
            is None
        ]
        if missing:
            raise RuntimeError(
                "database claims the safety-first baseline but required objects are "
                f"missing: {missing!r}; refusing a stamped or partial schema"
            )
        assert_catalog_artifacts(connection.execute(text(CATALOG_ARTIFACTS_SQL)).mappings())
        validate_database_extension_layout(
            connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL)).mappings(),
            baseline_installed=True,
        )
        reject_standalone_catalog_objects(baseline_installed=True)
        return validate_public_partition_layout(
            connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL)).mappings(),
            require_baseline_defaults=True,
        )

    validate_database_extension_layout(
        connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL)).mappings(),
        baseline_installed=False,
    )
    found = describe_public_application_relations(
        connection.execute(text(PUBLIC_APPLICATION_RELATIONS_SQL)).mappings()
    )
    if found:
        raise RuntimeError(
            "fresh-install-only migration refused unversioned non-empty target; "
            f"public relations found: {found!r}. Create a separate empty PostgreSQL "
            "database; no legacy compatibility path exists."
        )
    reject_standalone_catalog_objects(baseline_installed=False)
    return validate_public_partition_layout(
        connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL)).mappings(),
        require_baseline_defaults=False,
    )


def do_run_migrations(connection):
    # PostgreSQL exposes every child partition as a reflected table.  Only
    # names returned by the strict public layout validator may be hidden from
    # Alembic autogenerate; arbitrary inheritance can never bypass drift.
    partition_names = _validate_fresh_install_target(connection)
    # The catalog SELECT starts SQLAlchemy autobegin. End that read-only
    # transaction before Alembic opens its DDL transaction; otherwise the
    # surrounding connection context treats it as external and rolls the whole
    # migration back on close even though the command exits successfully.
    connection.commit()

    def include_name(name, type_, _parent_names):
        return not (type_ == "table" and name in partition_names)

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        compare_server_default=True,
        # Future forward revisions retain isolated transaction boundaries.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
