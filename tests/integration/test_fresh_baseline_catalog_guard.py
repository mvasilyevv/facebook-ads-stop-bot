"""Real PostgreSQL acceptance for the fresh-install catalog/layout guard."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from migrations.baseline_contract import (
    DATABASE_EXTENSION_LAYOUT_SQL,
    PUBLIC_PARTITION_LAYOUT_SQL,
    PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL,
    describe_standalone_public_catalog_objects,
    validate_database_extension_layout,
    validate_public_partition_layout,
)


def _object_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "ddl_template", "expected_detail"),
    [
        (
            "legacy_state",
            "CREATE TYPE public.{name} AS ENUM ('old')",
            "enum",
        ),
        (
            "legacy_code",
            "CREATE DOMAIN public.{name} AS text CHECK (VALUE <> '')",
            "domain",
        ),
        (
            "legacy_pair",
            "CREATE TYPE public.{name} AS (left_value integer, right_value text)",
            "composite",
        ),
        (
            "legacy_range",
            "CREATE TYPE public.{name} AS RANGE (subtype = integer)",
            "range",
        ),
        (
            "legacy_collation",
            "CREATE COLLATION public.{name} (provider = libc, locale = 'C')",
            "libc",
        ),
    ],
)
async def test_catalog_guard_finds_standalone_public_objects_created_by_ddl(
    pg_engine: AsyncEngine,
    prefix: str,
    ddl_template: str,
    expected_detail: str,
) -> None:
    name = _object_name(prefix)
    kind = "collation" if prefix == "legacy_collation" else "type"

    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.exec_driver_sql(ddl_template.format(name=name))
            rows = (
                await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))
            ).mappings()

            assert (
                f"{kind}:public.{name}({expected_detail})"
                in describe_standalone_public_catalog_objects(rows)
            )
        finally:
            # PostgreSQL DDL is transactional: the exact test object and all of
            # its generated companions disappear without touching baseline data.
            await transaction.rollback()


@pytest.mark.asyncio
async def test_catalog_guard_ignores_dependency_owned_table_row_and_array_types(
    pg_engine: AsyncEngine,
) -> None:
    name = _object_name("dependency_owned")

    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.exec_driver_sql(f"CREATE TABLE public.{name} (id integer)")
            rows = (
                await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))
            ).mappings()
            identities = describe_standalone_public_catalog_objects(rows)

            assert all(f"public.{name}(" not in identity for identity in identities)
            assert all(f"public._{name}(" not in identity for identity in identities)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_catalog_guard_ignores_extension_owned_types(
    pg_engine: AsyncEngine,
) -> None:
    async with pg_engine.connect() as connection:
        available = await connection.scalar(
            text(
                """
                SELECT 1
                FROM pg_catalog.pg_available_extensions
                WHERE name = 'hstore'
                """
            )
        )
        if available is None:
            pytest.skip("PostgreSQL hstore extension is unavailable")

        transaction = await connection.begin_nested()
        try:
            await connection.exec_driver_sql("CREATE EXTENSION hstore WITH SCHEMA public")
            rows = (
                await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))
            ).mappings()
            identities = describe_standalone_public_catalog_objects(rows)

            assert all("public.hstore(" not in identity for identity in identities)
            assert all("public.ghstore(" not in identity for identity in identities)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "ddl_templates", "identity_template"),
    [
        (
            "function",
            (
                "CREATE FUNCTION public.{name}(integer) RETURNS integer "
                "LANGUAGE sql IMMUTABLE AS 'SELECT $1'",
            ),
            "function:public.{name}(integer)(function)",
        ),
        (
            "procedure",
            ("CREATE PROCEDURE public.{name}() LANGUAGE sql AS 'SELECT 1'",),
            "procedure:public.{name}()(procedure)",
        ),
        (
            "aggregate",
            (
                "CREATE AGGREGATE public.{name}(integer) "
                "(SFUNC = pg_catalog.int4pl, STYPE = integer, INITCOND = '0')",
            ),
            "aggregate:public.{name}(integer)(aggregate)",
        ),
        (
            "operator",
            (
                "CREATE FUNCTION public.{name}_fn(integer, integer) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS 'SELECT $1 = $2'",
                "CREATE OPERATOR public.=== "
                "(FUNCTION = public.{name}_fn, LEFTARG = integer, RIGHTARG = integer)",
            ),
            'operator:public."==="(integer,integer)(binary)',
        ),
        (
            "conversion",
            ("CREATE CONVERSION public.{name} FOR 'BIG5' TO 'UTF8' FROM pg_catalog.big5_to_utf8",),
            "conversion:public.{name}(BIG5->UTF8 default=f)",
        ),
        (
            "text-search-config",
            ("CREATE TEXT SEARCH CONFIGURATION public.{name} (COPY = pg_catalog.simple)",),
            "text_search_configuration:public.{name}(configuration)",
        ),
        (
            "text-search-dictionary",
            ("CREATE TEXT SEARCH DICTIONARY public.{name} (TEMPLATE = pg_catalog.simple)",),
            "text_search_dictionary:public.{name}(dictionary)",
        ),
        (
            "operator-family",
            ("CREATE OPERATOR FAMILY public.{name} USING btree",),
            "operator_family:public.{name}(access_method=btree)",
        ),
        (
            "operator-class",
            (
                "CREATE OPERATOR FAMILY public.{name}_family USING btree",
                "CREATE OPERATOR CLASS public.{name} FOR TYPE integer USING btree "
                "FAMILY public.{name}_family AS "
                "OPERATOR 1 < (integer, integer), "
                "OPERATOR 2 <= (integer, integer), "
                "OPERATOR 3 = (integer, integer), "
                "OPERATOR 4 >= (integer, integer), "
                "OPERATOR 5 > (integer, integer), "
                "FUNCTION 1 pg_catalog.btint4cmp(integer, integer)",
            ),
            "operator_class:public.{name}(access_method=btree)",
        ),
    ],
)
async def test_catalog_guard_finds_other_standalone_public_objects(
    pg_engine: AsyncEngine,
    kind: str,
    ddl_templates: tuple[str, ...],
    identity_template: str,
) -> None:
    del kind
    name = _object_name("guard_object")
    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            for ddl in ddl_templates:
                await connection.exec_driver_sql(ddl.format(name=name))
            rows = (
                await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))
            ).mappings()

            assert identity_template.format(name=name) in (
                describe_standalone_public_catalog_objects(rows)
            )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ddl", "expected"),
    [
        (
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC",
            "default_acl:public.",
        ),
        (
            "GRANT CREATE ON SCHEMA public TO PUBLIC",
            "namespace_security:public.public(",
        ),
    ],
)
async def test_catalog_guard_finds_default_acl_and_namespace_security_drift(
    pg_engine: AsyncEngine,
    ddl: str,
    expected: str,
) -> None:
    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.exec_driver_sql(ddl)
            rows = (
                await connection.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))
            ).mappings()

            assert any(
                identity.startswith(expected)
                for identity in describe_standalone_public_catalog_objects(rows)
            )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_extension_policy_is_versioned_and_rejects_extra_extension_in_any_schema(
    pg_engine: AsyncEngine,
) -> None:
    schema_name = _object_name("extension_schema")
    async with pg_engine.connect() as connection:
        rows = (await connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL))).mappings()
        validate_database_extension_layout(rows, baseline_installed=True)

        available = await connection.scalar(
            text("SELECT 1 FROM pg_catalog.pg_available_extensions WHERE name = 'hstore'")
        )
        if available is None:
            pytest.skip("PostgreSQL hstore extension is unavailable")

        transaction = await connection.begin_nested()
        try:
            await connection.exec_driver_sql(f"CREATE SCHEMA {schema_name}")
            await connection.exec_driver_sql(f"CREATE EXTENSION hstore WITH SCHEMA {schema_name}")
            drifted = (await connection.execute(text(DATABASE_EXTENSION_LAYOUT_SQL))).mappings()
            with pytest.raises(RuntimeError, match=rf"unexpected extension {schema_name}.hstore"):
                validate_database_extension_layout(drifted, baseline_installed=True)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_runtime_partition_layout_matches_all_five_managed_parents(
    pg_engine: AsyncEngine,
) -> None:
    async with pg_engine.connect() as connection:
        rows = (await connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL))).mappings()
        hidden = validate_public_partition_layout(
            rows,
            require_baseline_defaults=True,
        )

    for parent in (
        "ad_metrics",
        "adsetpro_postback_events",
        "alert_events",
        "meta_api_audit_log",
        "scan_runs",
    ):
        assert f"{parent}_default" in hidden
        assert any(name.startswith(f"{parent}_20") for name in hidden)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ddl_templates", "expected"),
    [
        (
            (
                "CREATE TABLE public.ad_metrics_unreviewed "
                "PARTITION OF public.ad_metrics FOR VALUES FROM "
                "('2090-01-01') TO ('2090-02-01')",
            ),
            "unexpected runtime partition",
        ),
        (
            (
                "CREATE SCHEMA partition_shadow",
                "CREATE TABLE partition_shadow.ad_metrics_2091_01 "
                "PARTITION OF public.ad_metrics FOR VALUES FROM "
                "('2091-01-01') TO ('2091-02-01')",
            ),
            "cross-schema partition",
        ),
        (
            (
                "CREATE TABLE public.ad_metrics_2092_01 "
                "PARTITION OF public.ad_metrics FOR VALUES FROM "
                "('2092-01-01') TO ('2092-02-01') PARTITION BY RANGE (cycle_ts)",
            ),
            "nested partition child",
        ),
        (
            (
                "CREATE TABLE public.ad_metrics_2093_01 "
                "PARTITION OF public.ad_metrics FOR VALUES FROM "
                "('2093-02-01') TO ('2093-03-01')",
            ),
            "wrong calendar-month bound",
        ),
        (
            (
                "CREATE TABLE public.legacy_partition_parent (id integer)",
                "CREATE TABLE public.legacy_partition_child () "
                "INHERITS (public.legacy_partition_parent)",
            ),
            "non-partitioned parent inheritance",
        ),
        (
            (
                "CREATE TABLE public.unreviewed_partition_parent (created_at timestamptz) "
                "PARTITION BY RANGE (created_at)",
                "CREATE TABLE public.unreviewed_partition_parent_2094_01 "
                "PARTITION OF public.unreviewed_partition_parent FOR VALUES FROM "
                "('2094-01-01') TO ('2094-02-01')",
            ),
            "unexpected runtime partition",
        ),
    ],
)
async def test_partition_guard_rejects_noncanonical_real_catalog_layout(
    pg_engine: AsyncEngine,
    ddl_templates: tuple[str, ...],
    expected: str,
) -> None:
    async with pg_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            for ddl in ddl_templates:
                await connection.exec_driver_sql(ddl)
            rows = (await connection.execute(text(PUBLIC_PARTITION_LAYOUT_SQL))).mappings()

            with pytest.raises(RuntimeError, match=expected):
                validate_public_partition_layout(
                    rows,
                    require_baseline_defaults=True,
                )
        finally:
            await transaction.rollback()
