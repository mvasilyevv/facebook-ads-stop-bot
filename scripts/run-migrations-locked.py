#!/usr/bin/env python3
"""Run the fresh-install Alembic baseline under one PostgreSQL advisory lock."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import asyncpg
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from core.adset_pro.credentials import bootstrap_adsetpro_credentials_from_env
from core.telegram.service import bootstrap_telegram_config_from_env
from core.telegram.web_app_url import bootstrap_web_app_url_from_env
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

LOCK_ID = 6_646_244_731_681_337_901


async def acquire_lock(connection: asyncpg.Connection, lock_wait_seconds: float) -> None:
    deadline = time.monotonic() + lock_wait_seconds
    while time.monotonic() < deadline:
        if await connection.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_ID):
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"migration advisory lock was not acquired in {lock_wait_seconds:.0f}s")


async def monitor_lock_connection(
    connection: asyncpg.Connection, *, interval_seconds: float = 1.0
) -> None:
    """Keep proving that the session which owns the advisory lock is alive."""

    while True:
        await asyncio.sleep(interval_seconds)
        if connection.is_closed():
            raise ConnectionError("migration advisory-lock connection closed")
        await connection.fetchval("SELECT 1")


async def validate_fresh_install_target(
    connection: asyncpg.Connection,
) -> frozenset[str]:
    """Accept only an empty target or the exact already-installed baseline.

    This preflight is read-only and runs while the advisory lock is held.  It
    deliberately has no stamp, upgrade, drop or legacy conversion branch.
    """

    async def reject_standalone_catalog_objects(*, baseline_installed: bool) -> None:
        found = describe_standalone_public_catalog_objects(
            await connection.fetch(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL),
            allow_manifested_routines=baseline_installed,
        )
        if found:
            raise ValueError(
                "fresh-install-only migration refused standalone public catalog "
                f"objects: {found!r}. Create a separate empty PostgreSQL database; "
                "extension-owned and dependency-owned objects are ignored, but no "
                "legacy application type or collation is accepted."
            )

    version_table = await connection.fetchval("SELECT to_regclass('public.alembic_version')")
    revisions: list[str] = []
    if version_table is not None:
        rows = await connection.fetch(
            "SELECT version_num FROM public.alembic_version ORDER BY version_num"
        )
        revisions = [str(row["version_num"]) for row in rows]

    if revisions:
        if revisions != [BASELINE_REVISION]:
            raise ValueError(
                "fresh-install-only migration refused historical target; "
                f"expected revision {BASELINE_REVISION!r}, found {revisions!r}. "
                "Create a separate empty PostgreSQL database; this migrator never "
                "stamps, upgrades, drops or converts legacy data."
            )
        missing = [
            relation
            for relation in BASELINE_RELATION_SENTINELS
            if await connection.fetchval("SELECT to_regclass($1)", relation) is None
        ]
        if missing:
            raise ValueError(
                "database claims the safety-first baseline but required objects are "
                f"missing: {missing!r}; refusing a stamped or partial schema"
            )
        assert_catalog_artifacts(await connection.fetch(CATALOG_ARTIFACTS_SQL))
        validate_database_extension_layout(
            await connection.fetch(DATABASE_EXTENSION_LAYOUT_SQL),
            baseline_installed=True,
        )
        await reject_standalone_catalog_objects(baseline_installed=True)
        return validate_public_partition_layout(
            await connection.fetch(PUBLIC_PARTITION_LAYOUT_SQL),
            require_baseline_defaults=True,
        )

    validate_database_extension_layout(
        await connection.fetch(DATABASE_EXTENSION_LAYOUT_SQL),
        baseline_installed=False,
    )
    found = describe_public_application_relations(
        await connection.fetch(PUBLIC_APPLICATION_RELATIONS_SQL)
    )
    if found:
        raise ValueError(
            "fresh-install-only migration refused unversioned non-empty target; "
            f"public relations found: {found!r}. Create a separate empty PostgreSQL "
            "database; no legacy compatibility path exists."
        )
    await reject_standalone_catalog_objects(baseline_installed=False)
    return validate_public_partition_layout(
        await connection.fetch(PUBLIC_PARTITION_LAYOUT_SQL),
        require_baseline_defaults=False,
    )


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
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        command_timeout=max(timeout, 30),
    )
    process: asyncio.subprocess.Process | None = None
    monitor: asyncio.Task[None] | None = None
    loop: asyncio.AbstractEventLoop | None = None

    def forward_signal(received_signal: signal.Signals) -> None:
        if process is not None and process.returncode is None:
            process.send_signal(received_signal)

    try:
        await acquire_lock(connection, timeout)
        print("Migration advisory lock acquired", flush=True)
        await validate_fresh_install_target(connection)
        environment = dict(os.environ)
        environment.pop("WORKER_TYPE", None)
        environment["FB_AGENT_MIGRATION_DATABASE_URL"] = database_url

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, forward_signal, sig)
        monitor = asyncio.create_task(monitor_lock_connection(connection))

        for alembic_args in (("upgrade", "head"), ("check",)):
            print(f"Running alembic {' '.join(alembic_args)}", flush=True)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                *alembic_args,
                env=environment,
            )
            process_wait = asyncio.create_task(process.wait())
            done, _ = await asyncio.wait(
                {process_wait, monitor}, return_when=asyncio.FIRST_COMPLETED
            )
            if monitor in done:
                await monitor
                raise ConnectionError("migration advisory-lock monitor stopped")
            return_code = await process_wait
            if return_code != 0:
                return return_code
            process = None

        # Explicit one-shot import boundary. Runtime consumers never consult
        # these environment values; an existing row/tombstone remains
        # authoritative on every subsequent release.
        print("Importing initial DB-authoritative runtime configuration", flush=True)
        runtime_engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            hide_parameters=True,
        )
        try:
            await bootstrap_telegram_config_from_env(runtime_engine)
            await bootstrap_adsetpro_credentials_from_env(runtime_engine)
            await bootstrap_web_app_url_from_env(runtime_engine)
        finally:
            await runtime_engine.dispose()
        return 0
    finally:
        if loop is not None:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        if not connection.is_closed():
            try:
                await connection.execute("SELECT pg_advisory_unlock($1)", LOCK_ID)
            finally:
                await connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except (
        ConnectionError,
        KeyError,
        RuntimeError,
        ValueError,
        TimeoutError,
        asyncpg.PostgresError,
    ) as exc:
        print(f"ERROR: migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
