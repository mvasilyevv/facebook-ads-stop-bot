from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Column, Integer, String, Table, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.models import Base
from migrations.baseline_contract import BASELINE_REVISION
from migrations.revision_guard import RevisionContractError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run-migrations-locked.py"
SPEC = importlib.util.spec_from_file_location("run_migrations_locked_integration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MIGRATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATOR)


async def _run_locked_migrator(engine: AsyncEngine) -> tuple[int, str]:
    url = engine.url
    assert url.username and url.password and url.host and url.database
    environment = {
        **os.environ,
        "POSTGRES_HOST": url.host,
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_DB": url.database,
        "POSTGRES_USER": url.username,
        "POSTGRES_PASSWORD": url.password,
        "MIGRATION_LOCK_TIMEOUT_SECONDS": "20",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scripts.run-migrations-locked",
        cwd=ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_concurrent_noop_migrators_serialize_and_both_verify_head(
    pg_engine: AsyncEngine,
) -> None:
    first, second = await asyncio.gather(
        _run_locked_migrator(pg_engine),
        _run_locked_migrator(pg_engine),
    )

    for return_code, output in (first, second):
        assert return_code == 0, output
        assert "Migration advisory lock acquired" in output
        assert "No new upgrade operations detected." in output
        assert "database object contract verified" in output


def _temporary_forward_config(
    tmp_path: Path,
    *,
    revision: str,
    table_name: str,
    sleep_marker_key: str | None = None,
) -> tuple[Config, Table]:
    migration_root = tmp_path / f"migrations-{revision}"
    shutil.copytree(ROOT / "migrations", migration_root)
    sleep_statement = ""
    if sleep_marker_key is not None:
        sleep_statement = f"""
    op.execute(sa.text(\"\"\"
        SELECT pg_sleep(
            CASE WHEN EXISTS (
                SELECT 1 FROM public.system_config
                WHERE key = '{sleep_marker_key}'
            ) THEN 0::double precision ELSE 30::double precision END
        ) /* fb_agent_lock_loss_probe */
    \"\"\"))
"""
    (migration_root / "versions" / f"{revision}.py").write_text(
        f'''from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = "{BASELINE_REVISION}"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "{table_name}",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_{table_name}"),
    )
{sleep_statement}


def downgrade():
    raise RuntimeError("forward-only test revision")
''',
        encoding="utf-8",
    )
    table = Table(
        table_name,
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("payload", String(64), nullable=False),
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(migration_root))
    return config, table


async def _insert_system_marker(engine: AsyncEngine, key: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO public.system_config (id, key, value, description)
                VALUES (:id, :key, CAST(:value AS jsonb), 'migration test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """
            ),
            {"id": uuid.uuid4(), "key": key, "value": '{"preserved":true}'},
        )


async def _restore_baseline(
    engine: AsyncEngine,
    *,
    table: Table,
    marker_keys: tuple[str, ...] = (),
) -> None:
    async with engine.begin() as connection:
        await connection.execute(sa.schema.DropTable(table, if_exists=True))
        if marker_keys:
            await connection.execute(
                text("DELETE FROM public.system_config WHERE key = ANY(:keys)"),
                {"keys": list(marker_keys)},
            )
        await connection.execute(text("DELETE FROM public.alembic_version"))
        await connection.execute(
            text("INSERT INTO public.alembic_version (version_num) VALUES (:revision)"),
            {"revision": BASELINE_REVISION},
        )
    Base.metadata.remove(table)


@pytest.mark.asyncio
async def test_real_forward_revision_preserves_existing_data(
    pg_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    revision = "test_0002_forward"
    table_name = "forward_migration_probe"
    preserved_key = "migration_test_preserved"
    config, table = _temporary_forward_config(
        tmp_path,
        revision=revision,
        table_name=table_name,
    )
    await _insert_system_marker(pg_engine, preserved_key)
    try:
        assert (
            await MIGRATOR.run_locked_migrations(
                pg_engine,
                config=config,
                lock_wait_seconds=5,
            )
            == 0
        )
        async with pg_engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == revision
            )
            assert (
                await connection.scalar(
                    text("SELECT to_regclass(:relation)"),
                    {"relation": f"public.{table_name}"},
                )
                == table_name
            )
            assert (
                await connection.scalar(
                    text("SELECT value->>'preserved' FROM public.system_config WHERE key = :key"),
                    {"key": preserved_key},
                )
                == "true"
            )
    finally:
        await _restore_baseline(pg_engine, table=table, marker_keys=(preserved_key,))


async def _wait_for_sleeping_lock_owner(
    engine: AsyncEngine,
    migration_task: asyncio.Task[int],
) -> int:
    deadline = asyncio.get_running_loop().time() + 10
    expected_class_id = (MIGRATOR.LOCK_ID >> 32) & 0xFFFFFFFF
    expected_object_id = MIGRATOR.LOCK_ID & 0xFFFFFFFF
    observed: list[dict[str, object]] = []
    async with engine.connect() as connection:
        while asyncio.get_running_loop().time() < deadline:
            if migration_task.done():
                await migration_task
                raise AssertionError("migration completed before the lock-loss probe")
            observed = list(
                (
                    await connection.execute(
                        text(
                            """
                    SELECT activity.pid, activity.query, activity.state, activity.wait_event,
                           lock.classid::bigint AS class_id,
                           lock.objid::bigint AS object_id,
                           lock.objsubid
                    FROM pg_catalog.pg_stat_activity AS activity
                    LEFT JOIN pg_catalog.pg_locks AS lock
                      ON lock.pid = activity.pid
                     AND lock.locktype = 'advisory'
                     AND lock.granted
                    WHERE activity.datname = current_database()
                    """
                        )
                    )
                ).mappings()
            )
            # PostgreSQL caches cumulative-statistics values until the current
            # transaction ends.  Start a fresh read transaction on every poll
            # so a backend which acquired the lock after the first sample is
            # observable instead of waiting for its 30-second probe to finish.
            await connection.rollback()
            for row in observed:
                if (
                    "fb_agent_lock_loss_probe" in str(row["query"])
                    and row["wait_event"] == "PgSleep"
                    and row["class_id"] == expected_class_id
                    and row["object_id"] == expected_object_id
                    and row["objsubid"] == 1
                ):
                    return int(row["pid"])
            await asyncio.sleep(0.05)
    raise AssertionError(
        f"migration never reached the lock-held DDL transaction; observed={observed!r}"
    )


@pytest.mark.asyncio
async def test_lock_connection_loss_rolls_back_ddl_before_second_migrator(
    pg_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    revision = "test_0002_lock_loss"
    table_name = "migration_lock_loss_probe"
    skip_key = "migration_lock_loss_skip"
    config, table = _temporary_forward_config(
        tmp_path,
        revision=revision,
        table_name=table_name,
        sleep_marker_key=skip_key,
    )
    first = asyncio.create_task(
        MIGRATOR.run_locked_migrations(
            pg_engine,
            config=config,
            lock_wait_seconds=5,
        )
    )
    try:
        pid = await _wait_for_sleeping_lock_owner(pg_engine, first)
        async with pg_engine.begin() as killer:
            assert (
                await killer.scalar(
                    text("SELECT pg_terminate_backend(:pid)"),
                    {"pid": pid},
                )
                is True
            )
        with pytest.raises(SQLAlchemyError):
            await first

        async with pg_engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == BASELINE_REVISION
            )
            assert (
                await connection.scalar(
                    text("SELECT to_regclass(:relation)"),
                    {"relation": f"public.{table_name}"},
                )
                is None
            )

        await _insert_system_marker(pg_engine, skip_key)
        assert (
            await MIGRATOR.run_locked_migrations(
                pg_engine,
                config=config,
                lock_wait_seconds=5,
            )
            == 0
        )
        async with pg_engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == revision
            )
            assert (
                await connection.scalar(
                    text("SELECT to_regclass(:relation)"),
                    {"relation": f"public.{table_name}"},
                )
                == table_name
            )
    finally:
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        await _restore_baseline(pg_engine, table=table, marker_keys=(skip_key,))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_revisions",
    [
        ["unknown_revision"],
        [BASELINE_REVISION, "test_0002_reject"],
    ],
    ids=["unknown", "multiple"],
)
async def test_invalid_database_revision_is_rejected_before_ddl(
    pg_engine: AsyncEngine,
    tmp_path: Path,
    database_revisions: list[str],
) -> None:
    revision = "test_0002_reject"
    table_name = "rejected_migration_probe"
    config, table = _temporary_forward_config(
        tmp_path,
        revision=revision,
        table_name=table_name,
    )
    try:
        async with pg_engine.begin() as connection:
            await connection.execute(text("DELETE FROM public.alembic_version"))
            for database_revision in database_revisions:
                await connection.execute(
                    text("INSERT INTO public.alembic_version (version_num) VALUES (:revision)"),
                    {"revision": database_revision},
                )

        with pytest.raises(RevisionContractError):
            await MIGRATOR.run_locked_migrations(
                pg_engine,
                config=config,
                lock_wait_seconds=5,
            )
        async with pg_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT to_regclass(:relation)"),
                    {"relation": f"public.{table_name}"},
                )
                is None
            )
    finally:
        await _restore_baseline(pg_engine, table=table)
