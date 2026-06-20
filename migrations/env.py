# -*- coding: utf-8 -*-
"""Alembic env.py для async-миграций."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Импорт всего пакета core.models регистрирует все 35 ORM-моделей в Base.metadata.
# Этого достаточно для Alembic autogenerate — не нужно явно перечислять классы.
from core.models import Base  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL берём из настроек приложения (core.config), а не из захардкоженного
# alembic.ini — иначе в Docker/на сервере alembic идёт на localhost:5433 вместо
# хоста из POSTGRES_HOST (env), хотя apply_schema (тоже core.config) идёт верно.
# alembic.ini остаётся fallback'ом для офлайн-тулинга без окружения.
from core.config import get_settings  # noqa: E402

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
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
