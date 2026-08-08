"""Strict catalog contract for the frozen safety-first PostgreSQL baseline.

Alembic's revision marker and ORM drift check cannot prove that runtime-owned
functions, triggers, views, extensions and database-enforced CHECK constraints
still match the reviewed baseline.  Alembic does not autogenerate CHECK
constraint drift, so these constraints must be part of this explicit catalog
manifest.  This module is the single manifest used by Alembic, the locked
migrator and the host-side bootstrap guard.

Definitions come from PostgreSQL 16's ``pg_get_*def`` functions.  Catalog OIDs
and ownership are deliberately excluded.  Runs of whitespace are collapsed so
that PostgreSQL's harmless pretty-printing differences do not affect hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

BASELINE_REVISION = "0001_safety_first_baseline"

# PostgreSQL 16 creates plpgsql in every database.  It is the only extension
# accepted before the application baseline runs.  The baseline adds pgcrypto;
# both schema placement and extension version are immutable contract data.
# Any additional extension is an unreviewed executable/database surface and is
# therefore rejected regardless of which schema owns it.
EMPTY_DATABASE_EXTENSION_LAYOUT: dict[tuple[str, str], tuple[str, bool]] = {
    ("pg_catalog", "plpgsql"): ("1.0", False),
}
BASELINE_DATABASE_EXTENSION_LAYOUT: dict[tuple[str, str], tuple[str, bool]] = {
    **EMPTY_DATABASE_EXTENSION_LAYOUT,
    ("public", "pgcrypto"): ("1.3", True),
}

DATABASE_EXTENSION_LAYOUT_SQL = r"""
SELECT
    namespace.nspname::text AS extension_schema,
    extension.extname::text AS extension_name,
    extension.extversion::text AS extension_version,
    extension.extrelocatable AS extension_relocatable
FROM pg_catalog.pg_extension AS extension
JOIN pg_catalog.pg_namespace AS namespace
  ON namespace.oid = extension.extnamespace
ORDER BY namespace.nspname, extension.extname
"""

BASELINE_RELATION_SENTINELS = (
    "public.adset_duplicate_previews",
    "public.browser_channel_readiness",
    "public.browser_operation_capability_uses",
    "public.browser_operation_leases",
    "public.cabinet_runtime",
    "public.incidents",
    "public.notification_events",
    "public.operator_revision_state",
    "public.system_config",
    "public.task_queue",
)

PUBLIC_APPLICATION_RELATIONS_SQL = r"""
SELECT relation.relname, relation.relkind::text AS relkind
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace
  ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'c')
  AND relation.relname <> 'alembic_version'
  AND (
      relation.relkind <> 'c'
      OR NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_class'::pg_catalog.regclass
            AND dependency.objid = relation.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
  )
ORDER BY relation.relname
LIMIT 50
"""


def describe_public_application_relations(
    rows: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return stable identities for public relation-like catalog objects."""

    return [f"{row['relname']}({row['relkind']})" for row in rows]


def validate_database_extension_layout(
    rows: Iterable[Mapping[str, Any]],
    *,
    baseline_installed: bool,
) -> None:
    """Reject missing, moved, version-drifted or additional extensions."""

    expected = (
        BASELINE_DATABASE_EXTENSION_LAYOUT
        if baseline_installed
        else EMPTY_DATABASE_EXTENSION_LAYOUT
    )
    actual: dict[tuple[str, str], tuple[str, bool]] = {}
    for row in rows:
        identity = (str(row["extension_schema"]), str(row["extension_name"]))
        if identity in actual:
            raise RuntimeError(f"duplicate PostgreSQL extension identity: {identity!r}")
        actual[identity] = (
            str(row["extension_version"]),
            bool(row["extension_relocatable"]),
        )

    problems = [
        f"missing extension {schema}.{name}"
        for schema, name in sorted(expected.keys() - actual.keys())
    ]
    problems.extend(
        f"unexpected extension {schema}.{name}"
        for schema, name in sorted(actual.keys() - expected.keys())
    )
    problems.extend(
        "extension definition changed "
        f"{schema}.{name}: expected version={expected[(schema, name)][0]} "
        f"relocatable={str(expected[(schema, name)][1]).lower()}, "
        f"found version={actual[(schema, name)][0]} "
        f"relocatable={str(actual[(schema, name)][1]).lower()}"
        for schema, name in sorted(expected.keys() & actual.keys())
        if expected[(schema, name)] != actual[(schema, name)]
    )
    if problems:
        raise RuntimeError("safety-first PostgreSQL extension layout drift: " + "; ".join(problems))


# PostgreSQL creates dependency-owned companion types for tables, arrays and
# ranges, while extension members carry an extension dependency.  Everything
# below is a schema/database object that the relation-only empty-target guard
# cannot see.  Keep one shared query for every migration entrypoint.
PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL = r"""
WITH standalone_types AS (
    SELECT
        'type'::text AS object_kind,
        typ.typname::text AS object_name,
        CASE typ.typtype
            WHEN 'b' THEN 'base'
            WHEN 'c' THEN 'composite'
            WHEN 'd' THEN 'domain'
            WHEN 'e' THEN 'enum'
            WHEN 'm' THEN 'multirange'
            WHEN 'p' THEN 'pseudo'
            WHEN 'r' THEN 'range'
            ELSE typ.typtype::text
        END AS object_detail
    FROM pg_catalog.pg_type AS typ
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = typ.typnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_type'::pg_catalog.regclass
            AND dependency.objid = typ.oid
            AND dependency.objsubid = 0
            AND dependency.deptype <> 'n'
      )
),
standalone_routines AS (
    SELECT
        CASE procedure.prokind
            WHEN 'p' THEN 'procedure'
            WHEN 'a' THEN 'aggregate'
            WHEN 'w' THEN 'window_function'
            ELSE 'function'
        END::text AS object_kind,
        format(
            '%I(%s)',
            procedure.proname,
            pg_catalog.pg_get_function_identity_arguments(procedure.oid)
        ) AS object_name,
        CASE procedure.prokind
            WHEN 'p' THEN 'procedure'
            WHEN 'a' THEN 'aggregate'
            WHEN 'w' THEN 'window'
            ELSE 'function'
        END::text AS object_detail
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_proc'::pg_catalog.regclass
            AND dependency.objid = procedure.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_composite_relations AS (
    SELECT
        'type'::text AS object_kind,
        relation.relname::text AS object_name,
        'composite'::text AS object_detail
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind = 'c'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_class'::pg_catalog.regclass
            AND dependency.objid = relation.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_collations AS (
    SELECT
        'collation'::text AS object_kind,
        coll.collname::text AS object_name,
        CASE coll.collprovider
            WHEN 'b' THEN 'builtin'
            WHEN 'c' THEN 'libc'
            WHEN 'd' THEN 'default'
            WHEN 'i' THEN 'icu'
            ELSE coll.collprovider::text
        END AS object_detail
    FROM pg_catalog.pg_collation AS coll
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = coll.collnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_collation'::pg_catalog.regclass
            AND dependency.objid = coll.oid
            AND dependency.objsubid = 0
            AND dependency.deptype <> 'n'
      )
),
standalone_operators AS (
    SELECT
        'operator'::text AS object_kind,
        format(
            '%I(%s,%s)',
            operator_record.oprname,
            CASE
                WHEN operator_record.oprleft = 0 THEN 'NONE'
                ELSE pg_catalog.format_type(operator_record.oprleft, NULL)
            END,
            CASE
                WHEN operator_record.oprright = 0 THEN 'NONE'
                ELSE pg_catalog.format_type(operator_record.oprright, NULL)
            END
        ) AS object_name,
        CASE
            WHEN operator_record.oprleft = 0 THEN 'prefix'
            WHEN operator_record.oprright = 0 THEN 'postfix'
            ELSE 'binary'
        END::text AS object_detail
    FROM pg_catalog.pg_operator AS operator_record
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = operator_record.oprnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_operator'::pg_catalog.regclass
            AND dependency.objid = operator_record.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_conversions AS (
    SELECT
        'conversion'::text AS object_kind,
        conversion_record.conname::text AS object_name,
        format(
            '%s->%s default=%s',
            pg_catalog.pg_encoding_to_char(conversion_record.conforencoding),
            pg_catalog.pg_encoding_to_char(conversion_record.contoencoding),
            conversion_record.condefault
        ) AS object_detail
    FROM pg_catalog.pg_conversion AS conversion_record
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = conversion_record.connamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_conversion'::pg_catalog.regclass
            AND dependency.objid = conversion_record.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_text_search_objects AS (
    SELECT
        'text_search_configuration'::text AS object_kind,
        configuration.cfgname::text AS object_name,
        'configuration'::text AS object_detail
    FROM pg_catalog.pg_ts_config AS configuration
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = configuration.cfgnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_ts_config'::pg_catalog.regclass
            AND dependency.objid = configuration.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
    UNION ALL
    SELECT
        'text_search_dictionary'::text,
        dictionary.dictname::text,
        'dictionary'::text
    FROM pg_catalog.pg_ts_dict AS dictionary
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = dictionary.dictnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_ts_dict'::pg_catalog.regclass
            AND dependency.objid = dictionary.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
    UNION ALL
    SELECT
        'text_search_parser'::text,
        parser.prsname::text,
        'parser'::text
    FROM pg_catalog.pg_ts_parser AS parser
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = parser.prsnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_ts_parser'::pg_catalog.regclass
            AND dependency.objid = parser.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
    UNION ALL
    SELECT
        'text_search_template'::text,
        template.tmplname::text,
        'template'::text
    FROM pg_catalog.pg_ts_template AS template
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = template.tmplnamespace
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_ts_template'::pg_catalog.regclass
            AND dependency.objid = template.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_operator_classes AS (
    SELECT
        'operator_class'::text AS object_kind,
        operator_class.opcname::text AS object_name,
        format('access_method=%I', access_method.amname) AS object_detail
    FROM pg_catalog.pg_opclass AS operator_class
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = operator_class.opcnamespace
    JOIN pg_catalog.pg_am AS access_method
      ON access_method.oid = operator_class.opcmethod
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_opclass'::pg_catalog.regclass
            AND dependency.objid = operator_class.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
standalone_operator_families AS (
    SELECT
        'operator_family'::text AS object_kind,
        operator_family.opfname::text AS object_name,
        format('access_method=%I', access_method.amname) AS object_detail
    FROM pg_catalog.pg_opfamily AS operator_family
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = operator_family.opfnamespace
    JOIN pg_catalog.pg_am AS access_method
      ON access_method.oid = operator_family.opfmethod
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_opfamily'::pg_catalog.regclass
            AND dependency.objid = operator_family.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
nondefault_public_default_acls AS (
    SELECT
        'default_acl'::text AS object_kind,
        format(
            'role=%I scope=%s object_type=%s',
            pg_catalog.pg_get_userbyid(default_acl.defaclrole),
            CASE
                WHEN default_acl.defaclnamespace = 0 THEN 'global'
                ELSE 'public'
            END,
            default_acl.defaclobjtype
        ) AS object_name,
        default_acl.defaclacl::text AS object_detail
    FROM pg_catalog.pg_default_acl AS default_acl
    LEFT JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = default_acl.defaclnamespace
    WHERE (
            default_acl.defaclnamespace = 0
            OR namespace.nspname = 'public'
          )
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid =
                'pg_catalog.pg_default_acl'::pg_catalog.regclass
            AND dependency.objid = default_acl.oid
            AND dependency.objsubid = 0
            AND dependency.deptype = 'e'
      )
),
unsafe_public_namespace AS (
    SELECT
        'namespace_security'::text AS object_kind,
        namespace.nspname::text AS object_name,
        format(
            'owner=%I acl=%s',
            pg_catalog.pg_get_userbyid(namespace.nspowner),
            COALESCE(namespace.nspacl::text, 'NULL')
        ) AS object_detail
    FROM pg_catalog.pg_namespace AS namespace
    WHERE namespace.nspname = 'public'
      AND (
          namespace.nspowner <> 'pg_database_owner'::pg_catalog.regrole
          OR COALESCE(
              (
                  SELECT pg_catalog.array_agg(
                      format(
                          '%s:%s:%s:%s',
                          expanded_acl.grantor,
                          expanded_acl.grantee,
                          expanded_acl.privilege_type,
                          expanded_acl.is_grantable
                      )
                      ORDER BY
                          expanded_acl.grantor,
                          expanded_acl.grantee,
                          expanded_acl.privilege_type,
                          expanded_acl.is_grantable
                  )
                  FROM pg_catalog.aclexplode(namespace.nspacl) AS expanded_acl
              ),
              ARRAY[]::text[]
          ) <> ARRAY[
              format('%s:0:USAGE:f', namespace.nspowner),
              format(
                  '%s:%s:CREATE:f',
                  namespace.nspowner,
                  namespace.nspowner
              ),
              format(
                  '%s:%s:USAGE:f',
                  namespace.nspowner,
                  namespace.nspowner
              )
          ]::text[]
      )
)
SELECT object_kind, object_name, object_detail
FROM (
    SELECT * FROM standalone_types
    UNION ALL
    SELECT * FROM standalone_routines
    UNION ALL
    SELECT * FROM standalone_composite_relations
    UNION ALL
    SELECT * FROM standalone_collations
    UNION ALL
    SELECT * FROM standalone_operators
    UNION ALL
    SELECT * FROM standalone_conversions
    UNION ALL
    SELECT * FROM standalone_text_search_objects
    UNION ALL
    SELECT * FROM standalone_operator_classes
    UNION ALL
    SELECT * FROM standalone_operator_families
    UNION ALL
    SELECT * FROM nondefault_public_default_acls
    UNION ALL
    SELECT * FROM unsafe_public_namespace
) AS standalone_objects
ORDER BY object_kind, object_name
LIMIT 50
"""


def describe_standalone_public_catalog_objects(
    rows: Iterable[Mapping[str, Any]],
    *,
    allow_manifested_routines: bool = False,
) -> list[str]:
    """Return stable operator-facing identities for forbidden catalog objects."""

    identities: list[str] = []
    routine_kinds = {"function", "procedure", "aggregate", "window_function"}
    for row in rows:
        kind = str(row["object_kind"])
        name = str(row["object_name"])
        if (
            allow_manifested_routines
            and kind in routine_kinds
            and f"{kind}:public.{name}" in BASELINE_ARTIFACT_HASHES
        ):
            continue
        identities.append(f"{kind}:public.{name}({row['object_detail']})")
    return identities


BASELINE_DEFAULT_PARTITIONS: dict[str, str] = {
    "ad_metrics": "ad_metrics_default",
    "adsetpro_postback_events": "adsetpro_postback_events_default",
    "alert_events": "alert_events_default",
    "meta_api_audit_log": "meta_api_audit_log_default",
    "scan_runs": "scan_runs_default",
}

RUNTIME_MONTHLY_PARTITION_PARENTS = frozenset(BASELINE_DEFAULT_PARTITIONS)

PUBLIC_PARTITION_LAYOUT_SQL = r"""
SELECT
    parent_namespace.nspname::text AS parent_schema,
    parent.relname::text AS parent_name,
    parent.relkind::text AS parent_relkind,
    parent.oid::bigint AS parent_oid,
    child_namespace.nspname::text AS child_schema,
    child.relname::text AS child_name,
    child.relkind::text AS child_relkind,
    child.oid::bigint AS child_oid,
    child.relispartition AS child_is_partition,
    pg_catalog.pg_get_expr(child.relpartbound, child.oid, true) AS partition_bound
FROM pg_catalog.pg_inherits AS inheritance
JOIN pg_catalog.pg_class AS parent
  ON parent.oid = inheritance.inhparent
JOIN pg_catalog.pg_namespace AS parent_namespace
  ON parent_namespace.oid = parent.relnamespace
JOIN pg_catalog.pg_class AS child
  ON child.oid = inheritance.inhrelid
JOIN pg_catalog.pg_namespace AS child_namespace
  ON child_namespace.oid = child.relnamespace
WHERE parent.relkind IN ('r', 'p')
  AND child.relkind IN ('r', 'p')
  AND (
      parent_namespace.nspname = 'public'
      OR child_namespace.nspname = 'public'
  )
ORDER BY
    parent_namespace.nspname,
    parent.relname,
    child_namespace.nspname,
    child.relname
"""

_MONTHLY_PARTITION_NAME = re.compile(
    r"^(?P<parent>[a-z][a-z0-9_]*)_(?P<year>[0-9]{4})_(?P<month>0[1-9]|1[0-2])$"
)
_MONTHLY_PARTITION_BOUND = re.compile(
    r"^FOR VALUES FROM \('(?P<start>[^']+)'\) TO \('(?P<end>[^']+)'\)$"
)


def _next_month_start(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def validate_public_partition_layout(
    rows: Iterable[Mapping[str, Any]],
    *,
    require_baseline_defaults: bool,
) -> frozenset[str]:
    """Validate and return the exact public partitions hidden from Alembic.

    Only frozen DEFAULT partitions and canonical UTC calendar-month partitions
    for the five runtime-owned parents are accepted. Traditional inheritance,
    cross-schema attachments, wrong names/bounds and all other parents fail
    closed before Alembic can hide them from drift detection.
    """

    problems: list[str] = []
    reflected_partition_names: set[str] = set()
    found_defaults: set[str] = set()
    monthly_ranges: dict[str, list[tuple[datetime, datetime, str]]] = {}

    for row in rows:
        parent_schema = str(row["parent_schema"])
        parent_name = str(row["parent_name"])
        child_schema = str(row["child_schema"])
        child_name = str(row["child_name"])
        identity = f"{child_schema}.{child_name}->{parent_schema}.{parent_name}"

        # Objects wholly outside public are irrelevant and, critically, their
        # basename must never hide a public table during Alembic reflection.
        if parent_schema != "public" and child_schema != "public":
            continue
        if parent_schema != "public" or child_schema != "public":
            problems.append(f"cross-schema partition/inheritance {identity}")
            continue
        if str(row["parent_relkind"]) != "p":
            problems.append(f"non-partitioned parent inheritance {identity}")
            continue
        if str(row["child_relkind"]) != "r":
            problems.append(f"nested partition child {identity}")
            continue
        if not bool(row["child_is_partition"]):
            problems.append(f"traditional inheritance child {identity}")
            continue

        expected_default = BASELINE_DEFAULT_PARTITIONS.get(parent_name)
        if expected_default == child_name:
            if str(row["partition_bound"] or "") != "DEFAULT":
                problems.append(f"default partition has non-default bound {identity}")
                continue
            found_defaults.add(child_name)
            reflected_partition_names.add(child_name)
            continue

        match = _MONTHLY_PARTITION_NAME.fullmatch(child_name)
        if (
            parent_name not in RUNTIME_MONTHLY_PARTITION_PARENTS
            or match is None
            or match.group("parent") != parent_name
        ):
            problems.append(f"unexpected runtime partition {identity}")
            continue

        bound_match = _MONTHLY_PARTITION_BOUND.fullmatch(str(row["partition_bound"] or ""))
        if bound_match is None:
            problems.append(f"non-canonical monthly bound {identity}")
            continue
        try:
            start = datetime.fromisoformat(bound_match.group("start"))
            end = datetime.fromisoformat(bound_match.group("end"))
        except ValueError:
            problems.append(f"unparseable monthly bound {identity}")
            continue
        expected_start = datetime(
            int(match.group("year")),
            int(match.group("month")),
            1,
            tzinfo=UTC,
        )
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start.astimezone(UTC) != expected_start
            or end.astimezone(UTC) != _next_month_start(expected_start)
        ):
            problems.append(f"wrong calendar-month bound {identity}")
            continue
        monthly_ranges.setdefault(parent_name, []).append((start, end, child_name))
        reflected_partition_names.add(child_name)

    if require_baseline_defaults:
        missing_defaults = sorted(set(BASELINE_DEFAULT_PARTITIONS.values()) - found_defaults)
        problems.extend(
            f"missing frozen default partition public.{name}" for name in missing_defaults
        )

    for parent_name, ranges in monthly_ranges.items():
        previous_end: datetime | None = None
        for start, end, child_name in sorted(ranges):
            if previous_end is not None and start < previous_end:
                problems.append(f"overlapping monthly partition public.{child_name}->{parent_name}")
            previous_end = max(previous_end, end) if previous_end is not None else end

    if problems:
        raise RuntimeError("safety-first public partition layout drift: " + "; ".join(problems))
    return frozenset(reflected_partition_names)


# Named CHECK constraints are catalog artifacts because Alembic autogenerate
# does not compare them.  The relation name is part of the identity so inherited
# partition constraints remain independently verifiable.
BASELINE_CHECK_CONSTRAINT_HASHES: dict[str, str] = {
    "check_constraint:public.ad_alert_state.ck_ad_alert_state_enable_grace_coherent": "0722b7f2f428a6df0ef1298cd4f3bfcf604e5df20228b4e21c7eeb351d482b85",
    "check_constraint:public.ad_alert_state.ck_ad_alert_state_enable_grace_currency": "f7cab1aeca2efc180ebbf44a94b09e12d6d3b1d1c94f23fbdb83aae57fbb8de8",
    "check_constraint:public.ad_alert_state.ck_ad_alert_state_enable_grace_currency_exponent": "75b3bedb30bb5d33c20b37588f1d0ff272963b991cc4137c267276be5121949d",
    "check_constraint:public.ad_metrics.ck_ad_metrics_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.ad_metrics_default.ck_ad_metrics_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_consumption_coherent": "89960b2fe4b92c186d1725fa68e79328c31ea34a0debcb9048dc7137373f4b91",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_idempotency_key_format": "aa600d7c047367791d9ad43c58198fbf7071e767f904e37f3445f5ef826ca66c",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_plan_digest_sha256": "64361392c1de8a19d6fd9b5547485d6a8f71343d73c8d6d126235835abc19476",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_preview_object": "a2bf2bbfe90d079841d4532c0be4630b4b33c4065a81b8e5fe1888c5a3478ea4",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_principal_length": "a352a916c4fdf2947ab26f45f1dc196717c807b829c61d799308adb88da7d4b1",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_task_payload_object": "efe34cfcfda1eaa65a30b71fbe1fd12bd1e3356a1d716e31684480e996758bfa",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_token_digest_sha256": "9c588b3182d8e5a3af2206a47894c39acbc4f356650b797d545e925af00934b0",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_valid_consumed_at": "942e3b15d8e5e6bca0fca93930469a64acb43e487f3746600c666a306034c285",
    "check_constraint:public.adset_duplicate_previews.ck_adset_duplicate_previews_valid_expiry": "b6d28e6574467ae71fff63f32ecc5b4a0c63df5787c6bf2eea9b8dcbf9f9bb4a",
    "check_constraint:public.adsetpro_postback_events.ck_adsetpro_postback_events_adsetpro_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.adsetpro_postback_events.ck_adsetpro_postback_events_adsetpro_event_type": "d66568600c874f721f015f424f9768bbd2afc1b3f8513c9f97bb1011bf62ee6b",
    "check_constraint:public.adsetpro_postback_events_default.ck_adsetpro_postback_events_adsetpro_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.adsetpro_postback_events_default.ck_adsetpro_postback_events_adsetpro_event_type": "d66568600c874f721f015f424f9768bbd2afc1b3f8513c9f97bb1011bf62ee6b",
    "check_constraint:public.browser_channel_readiness.ck_browser_channel_readiness_channel": "f7f97e61923d6c28ce318f8c934f2e7001f7f8e3dfaa39515393d6c97d97b23f",
    "check_constraint:public.browser_channel_readiness.ck_browser_channel_readiness_evidence": "492abe6ae4dcadc23a661872cc2c21db1e0de95f8150fd38c5dcadf75fd8910b",
    "check_constraint:public.browser_channel_readiness.ck_browser_channel_readiness_state": "11570508107f344e66e6c84be9e9ce30c89900cfd41219cacb1d3133b730d768",
    "check_constraint:public.browser_operation_capability_uses.ck_browser_operation_capability_caller": "098a1c343ad46542a696bf73a8071a857b9c08b0212f308d8cf0137f26e472d7",
    "check_constraint:public.browser_operation_capability_uses.ck_browser_operation_capability_contract_version": "93f24241c45f7c88ede5c4ce620119bad86480848d9e712f42ef3d6a0eec7bff",
    "check_constraint:public.browser_operation_capability_uses.ck_browser_operation_capability_rpc": "05e767f0e1caecce09ea19d9c51afcfad09661b9c1a33960072bfe4d08121b18",
    "check_constraint:public.campaign_run.ck_campaign_run_status": "f701a2a09eddcb4d0885273586dfb132a2404b7cbaa1cb4508c84c616a6cd71d",
    "check_constraint:public.command_idempotency_receipts.ck_command_idem_receipt_action": "f8981069ac4c65bd3cd20e00415900c6f669505d7d707f658740b168ab6317b7",
    "check_constraint:public.fb_campaigns.ck_fb_campaigns_ad_account_identity": "d1ca82c3b03c3520075c03bb846d0617ca94176870a9daddcb8de2e16f0161ed",
    "check_constraint:public.incidents.ck_incidents_incident_generation_positive": "bc2700100fdddf45069df04e5f65317795bccb373545d2b39b28538a3d25c600",
    "check_constraint:public.incidents.ck_incidents_incident_severity": "48265b6c5f22ca8a90f0c090f5e32ebd5c702e8cec36b5fa7d25bd0acb555d3b",
    "check_constraint:public.incidents.ck_incidents_incident_status": "7e0fd7f792f5b85e2abc5811298d4d5e5a902b00d91be6f68636885eb37f10dc",
    "check_constraint:public.meta_account_snapshot.ck_meta_account_snapshot_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.meta_account_snapshot.ck_meta_account_snapshot_currency_observation": "5efab2e50c1420001f4c839a1bcbbec7371dca05700f02d6de04553baefc52c4",
    "check_constraint:public.meta_shadow_spend_state.ck_meta_shadow_spend_state_meta_shadow_baseline_complete": "e4d0abe82c47b514da880ec4d76004e6f1bea47a5fc7e3628989abd08cb6edea",
    "check_constraint:public.meta_shadow_spend_state.ck_meta_shadow_spend_state_candidate_requires_baseline": "cb908f068a2648ec9e2cc4c2a25ef69862434ce1e8f454abcf75c1838e1bfcd9",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_external_operation_kind": "bb741cdf89c4d9db5cf125898322b81bf886ae5d6ab464c7be6158149ae9ed34",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_notification_attempt_nonnegative": "f85cfd10445cc57f2e0f0c77a9085ac432215a54da5db03ac7a8d3291ca9c908",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_notification_bot_generation": "ffecadd763913b63c04e3a483f61bb330e0062d1329f0b198e2339f3c0ebb323",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_notification_delivery_state": "4b49a303830b4c98d5f6e87ef1be9684639d93cfa8a2e7408973b2f4a25a9141",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_notification_max_attempts_positive": "c98492c7b066a1c816f39d725a46d5e490a22d2aa01924ff24c00cfb5e74083e",
    "check_constraint:public.notification_deliveries.ck_notification_deliveries_notification_message_id_positive": "74eada0ea65c05d8e2bd3ad1bdb4fae2fcb776221db479643ec7ab1d84fc7008",
    "check_constraint:public.notification_events.ck_notification_events_notification_event_severity": "48265b6c5f22ca8a90f0c090f5e32ebd5c702e8cec36b5fa7d25bd0acb555d3b",
    "check_constraint:public.notification_events.ck_notification_events_notification_template_version_positive": "9fc7623f0b4f798144c6b97a0353644afdb26d9f522206739b7f18c299094a0f",
    "check_constraint:public.offer_rules.ck_offer_rules_cpa_currency_required": "06459149728eda57cdd6597dbf5ef8a4599f3586019cdac5a58e99f3f948cdb9",
    "check_constraint:public.offer_rules.ck_offer_rules_cpa_threshold_positive_finite": "99bf381b539863013dfba1f04c88fabc3ebf23588fa691a0e05522a0cbc749c2",
    "check_constraint:public.offer_rules.ck_offer_rules_currency": "b0bbd91e75ead142f3b6fe66360f3ca96c3fd3970acc673a509ee9e979abd533",
    "check_constraint:public.offer_rules.ck_offer_rules_frequency_threshold_positive_finite": "4c317bdfb22849d2c0678152383ea133045e79afc3eb97ee973adb36a98f0c6d",
    "check_constraint:public.offer_rules.ck_offer_rules_stop_percent_range": "3f491f67bb552b4736a7f89fe2dcfd48e13c04e9f8d2794fdc98d3dc246c1232",
    "check_constraint:public.offer_rules.ck_offer_rules_warning_percent_range": "0867a13d189bb6bbcc65a870830e476899c01f409a72e307f8d3ae44fc0a0bc0",
    "check_constraint:public.panel_login_tickets.ck_panel_login_tickets_positive_telegram_user_id": "47c0edfa4b09aca9e0bd9751e70e0e6ce730638d4e7dbaad474b8e3d1584dbd8",
    "check_constraint:public.panel_login_tickets.ck_panel_login_tickets_ticket_digest_sha256": "52f3df7c25b52c6827dfba305d6d2828b30bc2912fb30786e5339b5cd7df674f",
    "check_constraint:public.panel_login_tickets.ck_panel_login_tickets_valid_expiry": "a8bbe956460fc32d8e35f8b3b56b1df09119e86d0a6df08c6b7b1b0143479f55",
    "check_constraint:public.panel_oidc_attempts.ck_panel_oidc_attempts_state_digest_sha256": "1463c70a2d33c461e4774a0d7ea7c7e37f699789257d74a6913d8b4575352d47",
    "check_constraint:public.panel_oidc_attempts.ck_panel_oidc_attempts_valid_expiry": "b6d28e6574467ae71fff63f32ecc5b4a0c63df5787c6bf2eea9b8dcbf9f9bb4a",
    "check_constraint:public.panel_sessions.ck_panel_sessions_owner_role": "00bb27fcccd1a567f13bbfd4e3814dfcf6e9582382ed259607d3a11dfad54010",
    "check_constraint:public.panel_sessions.ck_panel_sessions_positive_telegram_user_id": "47c0edfa4b09aca9e0bd9751e70e0e6ce730638d4e7dbaad474b8e3d1584dbd8",
    "check_constraint:public.panel_sessions.ck_panel_sessions_token_digest_sha256": "9c588b3182d8e5a3af2206a47894c39acbc4f356650b797d545e925af00934b0",
    "check_constraint:public.panel_sessions.ck_panel_sessions_valid_expiry": "a8bbe956460fc32d8e35f8b3b56b1df09119e86d0a6df08c6b7b1b0143479f55",
    "check_constraint:public.scan_runs.ck_scan_runs_ad_account_identity": "d1ca82c3b03c3520075c03bb846d0617ca94176870a9daddcb8de2e16f0161ed",
    "check_constraint:public.scan_runs_default.ck_scan_runs_ad_account_identity": "d1ca82c3b03c3520075c03bb846d0617ca94176870a9daddcb8de2e16f0161ed",
    "check_constraint:public.task_queue.ck_task_queue_ck_task_queue_status": "4ec4d6bebc9f7ef7c1af94e80338783bab2e5ccabcdc26a00d3fe49d34bbc597",
    "check_constraint:public.task_queue.ck_task_queue_lane": "0f1049c24b4e20ebace3750c30849923a807571cdab171681a8fd9824611138b",
    "check_constraint:public.task_queue.ck_task_queue_meta_account_identity": "b07e0998b76214d12f52649d20a474c00daac8f98fcae0bb8d35c033ec5754f5",
    "check_constraint:public.task_queue.ck_task_queue_task_type": "c7d66e5df4c5b602c4ed43951fcb871ba641e94ca16bca0f102492ea75ee2a4a",
    "check_constraint:public.telegram_action_tokens.ck_telegram_action_tokens_telegram_action_generation": "88897ff418e429fd07a3894207b75e65e02d3876094c07e24c08f0411148582e",
    "check_constraint:public.telegram_action_tokens.ck_telegram_action_tokens_telegram_action_role": "e96b5adc3b9e3da5e3f8c4f094d51cfbbe6364bc14c377bdb0334db243ff6a04",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_attempts": "f85cfd10445cc57f2e0f0c77a9085ac432215a54da5db03ac7a8d3291ca9c908",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_bot__421d": "ffecadd763913b63c04e3a483f61bb330e0062d1329f0b198e2339f3c0ebb323",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_chat": "3673d88f7d70f5ff2da4c7b2a99807fd4dec52aa04635ac3cd417547515fa79e",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_max_attempts": "c98492c7b066a1c816f39d725a46d5e490a22d2aa01924ff24c00cfb5e74083e",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_message": "74eada0ea65c05d8e2bd3ad1bdb4fae2fcb776221db479643ec7ab1d84fc7008",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_ordinal": "4adff007406e95be262016584e2e34dfc657779bffd7fc2bb6567dfbb92e1d6b",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_parse_mode": "ee0fe9b93aef1d0221ec9d432e9b7b47677f1b78d841096e1ea4321e50594c5b",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_state": "029e35605aa0257f1bfa4aef36c0cfdb989b8558d3ff97da2e4d4cb564949652",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_target": "6f6fd4092ddac56a9c441945ebfdbeed00e2d8537bfbc5fbdc9e3ecf23a3a163",
    "check_constraint:public.telegram_command_replies.ck_telegram_command_replies_telegram_command_reply_text_length": "b438351f6da3db11b430094a2ed4aba06327e619e14a24776be01a07c34de0fc",
    "check_constraint:public.telegram_config.ck_telegram_config_bot_token_fingerprint_sha256": "8358c2529e3a6b9c1f671f9d3beb6ced6d5760dc7e5e9602a4d3074acd785512",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_attempt_count": "a7a294c605fe9415c2a4213eecd2ca56fe2cf50ce48bf32023b0591da4ae84ee",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_desired_url_https": "8f3fb85f5d4c75105eddbe4d214312594ce39eddae0c52737bfd1aa6f6c03783",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_generation": "ef9731b7d07162899b7ec79cbbae4dd6ad52bfdeccf7d72ab28f3d1557b9100d",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_operation": "fa85696190e1dafb8ee7173bd82c577c2a3ebe10190a6111a632ffadb8010aec",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_remote_pending_nonnegative": "5a30b85fe5387a7b3c3d0876f1fe0d1696ce42d9e6f455511f769911e4643ee6",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_secret_digest_sha256": "384162c4d50afb6910be5e9b66ce6535b7b00f68dc50d64cd73f24b3055d1454",
    "check_constraint:public.telegram_config.ck_telegram_config_webhook_state": "880e78051ffa2b61b2cca09d08c559908526b287f68dd58bde560c3445f17c0a",
    "check_constraint:public.telegram_invites.ck_telegram_invites_telegram_invite_role": "730b3f578bfb363bb868ba6df415fac5185874d141ce8eaab4ce80b045075bb1",
    "check_constraint:public.telegram_message_slots.ck_telegram_message_slots_telegram_message_slot_generation": "00d5fe16cafebd51e5eb260a00a45c2b797f03722c331dec382ee536f226c654",
    "check_constraint:public.telegram_message_slots.ck_telegram_message_slots_message_positive": "a383a71b3f2c8d2cad8268e3bed9d637f14062722a5753af52e373a84e1db935",
    "check_constraint:public.telegram_navigation_tokens.ck_telegram_navigation_tokens_telegram_navigation_target_kind": "ce7e5b3a38d45e5f106f7f97d8f48e4f7e4c2ed75657ed766369226107feef3c",
    "check_constraint:public.telegram_recipient_preferences.ck_telegram_recipient_preferences_telegram_preference_severity": "026f166805d1da7aa252641725146d98a88f4b69fced769cc3420df0816a1b42",
    "check_constraint:public.telegram_recipients.ck_telegram_recipients_telegram_recipient_role": "730b3f578bfb363bb868ba6df415fac5185874d141ce8eaab4ce80b045075bb1",
    "check_constraint:public.telegram_updates_inbox.ck_telegram_updates_inbox_telegram_update_attempt_nonnegative": "f85cfd10445cc57f2e0f0c77a9085ac432215a54da5db03ac7a8d3291ca9c908",
    "check_constraint:public.telegram_updates_inbox.ck_telegram_updates_inbox_telegram_update_bot_generation": "ffecadd763913b63c04e3a483f61bb330e0062d1329f0b198e2339f3c0ebb323",
    "check_constraint:public.telegram_updates_inbox.ck_telegram_updates_inbox_telegram_update_inbox_state": "088e2e8269f4de32d55402f2c03d3c4c1825a0966e36e986ab1e98d0baac14db",
}

# The exact application-owned catalog surface created by the frozen baseline.
# Extension-owned pgcrypto routines are excluded from the function branch and
# represented by the extension artifact itself.
BASELINE_ARTIFACT_HASHES: dict[str, str] = {
    **BASELINE_CHECK_CONSTRAINT_HASHES,
    "extension:pg_catalog.plpgsql": (
        "c2304d3f5ae349dbac29cc56b49f8a2e9f13d65448953f303469a2eea68d32be"
    ),
    "extension:public.pgcrypto": (
        "f4454d2c6b12d37e77ab528bf2c67987ed7cc7888f8be648979f1b2290da919a"
    ),
    "function:public.bump_fb_operator_revision(event_scope text, event_id text)": (
        "d60fb13961bf4f375d314308bedaa8f501a04271d61fd4fdc1420a915d0c252a"
    ),
    "function:public.enforce_adset_duplicate_preview_consume_once()": (
        "ed4e29dbde236d152bcaf10a814d52f1156585c2238586eb1c5e7c9c4a13b2d7"
    ),
    "function:public.invalidate_browser_readiness_on_maintenance()": (
        "714d23e5880c6f11acd1dde3f517dd151dc945ab7e85557961bf47cd17b1601c"
    ),
    "function:public.notify_fb_operator_event()": (
        "23640190055bf75e23efdaafaefa779ab11a16277d9524afc0a3975316364a32"
    ),
    "function:public.notify_fb_operator_statement()": (
        "958fdc11bd2e8149b5eb8b3835ea144c149b46ba6c51db41d6ec7f0af501eb03"
    ),
    "trigger:public.ad_metrics.trg_ad_metrics_operator_revision": (
        "4b965495dc0e831ca64d8c87b0de726ccc00a6d563d8f83622f712c90a9c9863"
    ),
    "trigger:public.adset_duplicate_previews.trg_adset_duplicate_previews_consume_once": (
        "ff0847b577c06a6efeddbb65a311cfc9328e212aba8dcbe331a3a172f6c3b04c"
    ),
    "trigger:public.cabinet_runtime.trg_cabinet_runtime_operator_notify": (
        "cfefdd8253cb04f7c3b09fe570d59faefccef594f82fbd91801525efafd194ff"
    ),
    "trigger:public.campaign_run.trg_campaign_run_operator_notify": (
        "38cf6e5101035bfb71daaa55c22eff859c257eeb22337c3db61cdc1463e8af1c"
    ),
    "trigger:public.fb_ads.trg_fb_ads_operator_revision": (
        "a6fad0347cf5ddd7784e4d4655f40c7034eabcd0374160ee645cdbdd838d337d"
    ),
    "trigger:public.incidents.trg_incidents_operator_notify": (
        "0cc0776bf31f6cc70c54f714fdc7f352584b11f0b8f3df4fcd8e537c02e7ed80"
    ),
    "trigger:public.notification_deliveries.trg_notification_deliveries_operator_notify": (
        "ad3bd79771d7231527693afb0743d6fdedbf613ece8e73c70606077c35b9785d"
    ),
    "trigger:public.observer_config.trg_observer_config_operator_revision": (
        "4ec9d8e5e21f8c3d3677697ce83d6b15813ede5936a07869232865350396846e"
    ),
    "trigger:public.scan_runs.trg_scan_runs_operator_revision": (
        "268a69666cabcf8838c535da667649750096505f4fc8ff3e76a364192a70f038"
    ),
    "trigger:public.system_config.trg_system_config_browser_maintenance_readiness": (
        "f4e492b367666a638e09c82fc88379151284bfabc7c7007f3993aeedc87a35f7"
    ),
    "trigger:public.task_queue.trg_task_queue_operator_notify": (
        "86db136c4a305cf520f737b35894832068a1805cf901d6609ec44e7afe229693"
    ),
    "trigger:public.tracker_click_state.trg_tracker_click_state_operator_revision": (
        "7719be2774a34ba6792f61188b52f5c1b58586af712ead43bd56cfef7d22667f"
    ),
    "view:public.operator_revision_state": (
        "88313c992e8408dc7ae5b30224ff2d833fc7e65aaaf0c9b24b2716a8b1543875"
    ),
}

CATALOG_ARTIFACTS_SQL = r"""
WITH application_routines AS (
    SELECT
        CASE procedure.prokind
            WHEN 'p' THEN 'procedure'
            WHEN 'a' THEN 'aggregate'
            WHEN 'w' THEN 'window_function'
            ELSE 'function'
        END::text AS artifact_kind,
        format(
            '%I.%I(%s)',
            namespace.nspname,
            procedure.proname,
            pg_catalog.pg_get_function_identity_arguments(procedure.oid)
        ) AS artifact_identity,
        CASE
            WHEN procedure.prokind IN ('f', 'p') THEN
                pg_catalog.regexp_replace(
                    pg_catalog.btrim(
                        pg_catalog.pg_get_functiondef(procedure.oid)
                    ),
                    '[[:space:]]+',
                    ' ',
                    'g'
                )
            ELSE
                format(
                    'PROKIND=%s IDENTITY=%s RESULT=%s LANGUAGE=%I '
                    'VOLATILITY=%s PARALLEL=%s SECURITY_DEFINER=%s '
                    'LEAKPROOF=%s STRICT=%s',
                    procedure.prokind,
                    pg_catalog.pg_get_function_identity_arguments(procedure.oid),
                    pg_catalog.pg_get_function_result(procedure.oid),
                    language.lanname,
                    procedure.provolatile,
                    procedure.proparallel,
                    procedure.prosecdef,
                    procedure.proleakproof,
                    procedure.proisstrict
                )
        END AS normalized_definition
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    JOIN pg_catalog.pg_language AS language
      ON language.oid = procedure.prolang
    WHERE namespace.nspname = 'public'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependency
          WHERE dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
            AND dependency.objid = procedure.oid
            AND dependency.deptype = 'e'
      )
),
application_triggers AS (
    SELECT
        'trigger'::text AS artifact_kind,
        format(
            '%I.%I.%I',
            namespace.nspname,
            relation.relname,
            trigger.tgname
        ) AS artifact_identity,
        pg_catalog.regexp_replace(
            pg_catalog.btrim(
                pg_catalog.pg_get_triggerdef(trigger.oid, false)
                || ' ENABLED='
                || trigger.tgenabled::text
            ),
            '[[:space:]]+',
            ' ',
            'g'
        ) AS normalized_definition
    FROM pg_catalog.pg_trigger AS trigger
    JOIN pg_catalog.pg_class AS relation
      ON relation.oid = trigger.tgrelid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND NOT trigger.tgisinternal
),
application_views AS (
    SELECT
        CASE relation.relkind
            WHEN 'm' THEN 'materialized_view'
            ELSE 'view'
        END::text AS artifact_kind,
        format('%I.%I', namespace.nspname, relation.relname) AS artifact_identity,
        pg_catalog.regexp_replace(
            pg_catalog.btrim(
                pg_catalog.pg_get_viewdef(relation.oid, false)
                || ' OPTIONS='
                || COALESCE(relation.reloptions::text, '')
            ),
            '[[:space:]]+',
            ' ',
            'g'
        ) AS normalized_definition
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('v', 'm')
),
application_check_constraints AS (
    SELECT
        'check_constraint'::text AS artifact_kind,
        format(
            '%I.%I.%I',
            namespace.nspname,
            relation.relname,
            constraint_record.conname
        ) AS artifact_identity,
        pg_catalog.regexp_replace(
            pg_catalog.btrim(
                pg_catalog.pg_get_constraintdef(constraint_record.oid, false)
                || ' VALIDATED='
                || constraint_record.convalidated::text
                || ' NOINHERIT='
                || constraint_record.connoinherit::text
            ),
            '[[:space:]]+',
            ' ',
            'g'
        ) AS normalized_definition
    FROM pg_catalog.pg_constraint AS constraint_record
    JOIN pg_catalog.pg_class AS relation
      ON relation.oid = constraint_record.conrelid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND constraint_record.contype = 'c'
      AND relation.relkind IN ('r', 'p')
      -- Monthly partitions are runtime-owned rolling data surfaces. Their
      -- inherited/copied CHECK constraints repeat the frozen parent
      -- definition under time-dependent relation names, so including them
      -- would make the catalog guard fail after normal partition rotation.
      -- Frozen default partitions remain explicit baseline artifacts.
      AND (
          NOT relation.relispartition
          OR relation.relname LIKE '%\_default' ESCAPE '\'
      )
),
database_extensions AS (
    SELECT
        'extension'::text AS artifact_kind,
        format('%I.%I', namespace.nspname, extension.extname) AS artifact_identity,
        format(
            'EXTENSION %I SCHEMA %I VERSION %L RELOCATABLE=%s',
            extension.extname,
            namespace.nspname,
            extension.extversion,
            extension.extrelocatable
        ) AS normalized_definition
    FROM pg_catalog.pg_extension AS extension
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = extension.extnamespace
),
artifacts AS (
    SELECT * FROM application_routines
    UNION ALL
    SELECT * FROM application_triggers
    UNION ALL
    SELECT * FROM application_views
    UNION ALL
    SELECT * FROM application_check_constraints
    UNION ALL
    SELECT * FROM database_extensions
)
SELECT artifact_kind, artifact_identity, normalized_definition
FROM artifacts
ORDER BY artifact_kind, artifact_identity
"""


def artifact_key(kind: str, identity: str) -> str:
    """Return the unambiguous manifest key for one catalog object."""

    return f"{kind}:{identity}"


def definition_sha256(definition: str) -> str:
    """Hash one already-normalized PostgreSQL catalog definition."""

    return hashlib.sha256(definition.encode("utf-8")).hexdigest()


def catalog_artifact_hashes(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Convert catalog query rows into the exact comparable manifest."""

    result: dict[str, str] = {}
    for row in rows:
        key = artifact_key(
            str(row["artifact_kind"]),
            str(row["artifact_identity"]),
        )
        if key in result:
            raise RuntimeError(f"duplicate baseline catalog artifact: {key}")
        result[key] = definition_sha256(str(row["normalized_definition"]))
    return dict(sorted(result.items()))


def catalog_artifact_drift(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Describe missing, unexpected and definition-drifted artifacts."""

    actual = catalog_artifact_hashes(rows)
    expected = BASELINE_ARTIFACT_HASHES
    messages = [f"missing {key}" for key in sorted(expected.keys() - actual.keys())]
    messages.extend(f"unexpected {key}" for key in sorted(actual.keys() - expected.keys()))
    messages.extend(
        f"definition changed {key}"
        for key in sorted(expected.keys() & actual.keys())
        if expected[key] != actual[key]
    )
    return messages


def assert_catalog_artifacts(rows: Iterable[Mapping[str, Any]]) -> None:
    """Fail closed unless catalog artifacts exactly equal the manifest."""

    drift = catalog_artifact_drift(rows)
    if drift:
        raise RuntimeError("safety-first baseline catalog artifact drift: " + "; ".join(drift))


def _render_extension_values(
    layout: Mapping[tuple[str, str], tuple[str, bool]],
) -> str:
    values = []
    for (schema, name), (version, relocatable) in sorted(layout.items()):
        values.append(
            "("
            + ", ".join(
                (
                    f"'{schema}'::text",
                    f"'{name}'::text",
                    f"'{version}'::text",
                    f"{str(relocatable).lower()}::boolean",
                )
            )
            + ")"
        )
    return ",\n        ".join(values)


def render_psql_fresh_target_guard() -> str:
    """Render the shared fresh/exact-target preflight for host-side psql."""

    sentinels = ",\n        ".join(f"'{name}'::text" for name in BASELINE_RELATION_SENTINELS)
    empty_extensions = _render_extension_values(EMPTY_DATABASE_EXTENSION_LAYOUT)
    return f"""
SELECT pg_catalog.set_config('fb_agent.require_empty', :'require_empty', false);
SELECT pg_catalog.set_config(
  'fb_agent.expected_bootstrap_cluster_id',
  :'expected_cluster_id',
  false
);
DO $fresh_target_guard$
DECLARE
  revisions text[] := ARRAY[]::text[];
  relations text[] := ARRAY[]::text[];
  catalog_objects text[] := ARRAY[]::text[];
  extension_drift text[] := ARRAY[]::text[];
  missing_sentinels text[] := ARRAY[]::text[];
  require_empty boolean :=
    pg_catalog.current_setting('fb_agent.require_empty')::boolean;
  expected_cluster_id text :=
    pg_catalog.current_setting('fb_agent.expected_bootstrap_cluster_id');
  actual_cluster_id text := NULLIF(
    pg_catalog.current_setting('fb_agent.bootstrap_cluster_id', true),
    ''
  );
BEGIN
  IF actual_cluster_id IS NOT NULL AND actual_cluster_id <> expected_cluster_id THEN
    RAISE EXCEPTION 'database belongs to a different bootstrap cluster';
  END IF;

  IF pg_catalog.to_regclass('public.alembic_version') IS NOT NULL THEN
    SELECT COALESCE(
        pg_catalog.array_agg(version_num ORDER BY version_num),
        ARRAY[]::text[]
      )
      INTO revisions
      FROM public.alembic_version;
  END IF;

  WITH public_relations AS (
{_indent_sql(PUBLIC_APPLICATION_RELATIONS_SQL.rstrip(), 4)}
  )
  SELECT COALESCE(
      pg_catalog.array_agg(
        pg_catalog.format('%I(%s)', relname, relkind)
        ORDER BY relname, relkind
      ),
      ARRAY[]::text[]
    )
    INTO relations
    FROM public_relations;

  WITH standalone_objects AS (
{_indent_sql(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL.rstrip(), 4)}
  )
  SELECT COALESCE(
      pg_catalog.array_agg(
        pg_catalog.format(
          '%s:public.%I(%s)',
          object_kind,
          object_name,
          object_detail
        )
        ORDER BY object_kind, object_name
      ),
      ARRAY[]::text[]
    )
    INTO catalog_objects
    FROM standalone_objects
    WHERE pg_catalog.cardinality(revisions) = 0
       OR object_kind NOT IN (
            'function', 'procedure', 'aggregate', 'window_function'
          );

  IF pg_catalog.cardinality(revisions) = 0 THEN
    WITH actual_extensions AS (
{_indent_sql(DATABASE_EXTENSION_LAYOUT_SQL.rstrip(), 6)}
    ),
    expected_extensions(
      extension_schema,
      extension_name,
      extension_version,
      extension_relocatable
    ) AS (
      VALUES
        {empty_extensions}
    ),
    differences AS (
      SELECT CASE
        WHEN actual.extension_name IS NULL THEN
          'missing extension ' || expected.extension_schema || '.' ||
          expected.extension_name
        WHEN expected.extension_name IS NULL THEN
          'unexpected extension ' || actual.extension_schema || '.' ||
          actual.extension_name
        ELSE
          'extension definition changed ' || actual.extension_schema || '.' ||
          actual.extension_name
      END AS message
      FROM actual_extensions AS actual
      FULL JOIN expected_extensions AS expected
        USING (extension_schema, extension_name)
      WHERE actual.extension_name IS NULL
         OR expected.extension_name IS NULL
         OR actual.extension_version IS DISTINCT FROM expected.extension_version
         OR actual.extension_relocatable IS DISTINCT FROM expected.extension_relocatable
    )
    SELECT COALESCE(
        pg_catalog.array_agg(message ORDER BY message),
        ARRAY[]::text[]
      )
      INTO extension_drift
      FROM differences;
  END IF;

  IF require_empty
    AND (
      pg_catalog.cardinality(revisions) > 0
      OR pg_catalog.cardinality(relations) > 0
      OR pg_catalog.cardinality(catalog_objects) > 0
      OR pg_catalog.cardinality(extension_drift) > 0
    )
    AND actual_cluster_id IS DISTINCT FROM expected_cluster_id
  THEN
    RAISE EXCEPTION
      'initial installation rejects a non-owned database; revisions=%, relations=%, catalog_objects=%, extension_drift=%',
      revisions, relations, catalog_objects, extension_drift;
  ELSIF pg_catalog.cardinality(revisions) > 0 THEN
    IF revisions <> ARRAY['{BASELINE_REVISION}']::text[] THEN
      RAISE EXCEPTION
        'fresh-install-only target rejected historical revisions: %', revisions;
    END IF;
    SELECT COALESCE(
        pg_catalog.array_agg(name ORDER BY name),
        ARRAY[]::text[]
      )
      INTO missing_sentinels
      FROM unnest(ARRAY[
        {sentinels}
      ]) AS sentinel(name)
      WHERE pg_catalog.to_regclass(name) IS NULL;
    IF pg_catalog.cardinality(missing_sentinels) > 0 THEN
      RAISE EXCEPTION
        'target claims {BASELINE_REVISION} but sentinels are missing: %',
        missing_sentinels;
    END IF;
    IF pg_catalog.cardinality(catalog_objects) > 0 THEN
      RAISE EXCEPTION
        'fresh-install-only target rejected standalone public catalog objects: %',
        catalog_objects;
    END IF;
    RETURN;
  END IF;

  IF pg_catalog.cardinality(relations) > 0 THEN
    RAISE EXCEPTION
      'fresh-install-only target rejected unversioned public relations: %', relations;
  END IF;
  IF pg_catalog.cardinality(catalog_objects) > 0 THEN
    RAISE EXCEPTION
      'fresh-install-only target rejected standalone public catalog objects: %',
      catalog_objects;
  END IF;
  IF pg_catalog.cardinality(extension_drift) > 0 THEN
    RAISE EXCEPTION
      'fresh-install-only target rejected PostgreSQL extension layout: %',
      extension_drift;
  END IF;
END
$fresh_target_guard$;
""".strip()


def render_psql_cluster_ownership_commit() -> str:
    """Persist bootstrap ownership only after every shared guard has passed."""

    return r"""
SELECT pg_catalog.format(
  'ALTER DATABASE %I SET "fb_agent.bootstrap_cluster_id" TO %L',
  pg_catalog.current_database(),
  :'expected_cluster_id'
) \gexec
SELECT pg_catalog.set_config(
  'fb_agent.bootstrap_cluster_id',
  :'expected_cluster_id',
  false
);
""".strip()


def render_psql_catalog_guard() -> str:
    """Render the same manifest as a self-contained PostgreSQL DO block."""

    expected_json = json.dumps(
        BASELINE_ARTIFACT_HASHES,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("'", "''")
    return f"""
DO $baseline_catalog_guard$
DECLARE
  revisions text[] := ARRAY[]::text[];
  expected jsonb := '{expected_json}'::jsonb;
  actual jsonb := '{{}}'::jsonb;
  drift text[] := ARRAY[]::text[];
BEGIN
  IF pg_catalog.to_regclass('public.alembic_version') IS NULL THEN
    RETURN;
  END IF;
  SELECT COALESCE(
      pg_catalog.array_agg(version_num ORDER BY version_num),
      ARRAY[]::text[]
    )
    INTO revisions
    FROM public.alembic_version;
  IF pg_catalog.cardinality(revisions) = 0 THEN
    RETURN;
  END IF;
  IF revisions <> ARRAY['{BASELINE_REVISION}']::text[] THEN
    RAISE EXCEPTION
      'catalog guard requires exact baseline revision {BASELINE_REVISION}; found %',
      revisions;
  END IF;

  WITH catalog_artifacts AS (
{_indent_sql(CATALOG_ARTIFACTS_SQL.rstrip(), 4)}
  )
  SELECT COALESCE(
      pg_catalog.jsonb_object_agg(
        artifact_kind || ':' || artifact_identity,
        pg_catalog.encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(normalized_definition, 'UTF8')
          ),
          'hex'
        )
      ),
      '{{}}'::jsonb
    )
    INTO actual
    FROM catalog_artifacts;

  SELECT COALESCE(
      pg_catalog.array_agg(message ORDER BY message),
      ARRAY[]::text[]
    )
    INTO drift
    FROM (
      SELECT 'missing ' || expected_item.key AS message
      FROM pg_catalog.jsonb_each_text(expected) AS expected_item
      WHERE NOT actual ? expected_item.key
      UNION ALL
      SELECT 'unexpected ' || actual_item.key AS message
      FROM pg_catalog.jsonb_each_text(actual) AS actual_item
      WHERE NOT expected ? actual_item.key
      UNION ALL
      SELECT 'definition changed ' || expected_item.key AS message
      FROM pg_catalog.jsonb_each_text(expected) AS expected_item
      JOIN pg_catalog.jsonb_each_text(actual) AS actual_item
        ON actual_item.key = expected_item.key
      WHERE actual_item.value <> expected_item.value
    ) AS differences;

  IF pg_catalog.cardinality(drift) > 0 THEN
    RAISE EXCEPTION
      'safety-first baseline catalog artifact drift: %',
      pg_catalog.array_to_string(drift, '; ');
  END IF;
END
$baseline_catalog_guard$;
""".strip()


def render_psql_partition_guard() -> str:
    """Render strict validation for frozen defaults and runtime month partitions."""

    defaults = json.dumps(BASELINE_DEFAULT_PARTITIONS, sort_keys=True).replace("'", "''")
    runtime_parents = ", ".join(
        f"'{parent}'" for parent in sorted(RUNTIME_MONTHLY_PARTITION_PARENTS)
    )
    return f"""
DO $baseline_partition_guard$
DECLARE
  revisions text[] := ARRAY[]::text[];
  defaults jsonb := '{defaults}'::jsonb;
  found_defaults text[] := ARRAY[]::text[];
  problems text[] := ARRAY[]::text[];
  overlap_problems text[] := ARRAY[]::text[];
  partition_row record;
  expected_default text;
  expected_start timestamptz;
  expected_end timestamptz;
  actual_start timestamptz;
  actual_end timestamptz;
  bound_parts text[];
  name_parts text[];
BEGIN
  IF pg_catalog.to_regclass('public.alembic_version') IS NULL THEN
    RETURN;
  END IF;
  SELECT COALESCE(
      pg_catalog.array_agg(version_num ORDER BY version_num),
      ARRAY[]::text[]
    )
    INTO revisions
    FROM public.alembic_version;
  IF pg_catalog.cardinality(revisions) = 0 THEN
    RETURN;
  END IF;
  IF revisions <> ARRAY['{BASELINE_REVISION}']::text[] THEN
    RAISE EXCEPTION
      'partition guard requires exact baseline revision {BASELINE_REVISION}; found %',
      revisions;
  END IF;

  FOR partition_row IN
{_indent_sql(PUBLIC_PARTITION_LAYOUT_SQL.rstrip(), 4)}
  LOOP
    IF partition_row.parent_schema <> 'public'
       OR partition_row.child_schema <> 'public' THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'cross-schema partition/inheritance %I.%I->%I.%I',
          partition_row.child_schema,
          partition_row.child_name,
          partition_row.parent_schema,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    IF partition_row.parent_relkind <> 'p' THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'non-partitioned parent inheritance public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    IF partition_row.child_relkind <> 'r' THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'nested partition child public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    IF NOT partition_row.child_is_partition THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'traditional inheritance child public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;

    expected_default := defaults ->> partition_row.parent_name;
    IF expected_default = partition_row.child_name THEN
      IF partition_row.partition_bound <> 'DEFAULT' THEN
        problems := pg_catalog.array_append(
          problems,
          pg_catalog.format(
            'default partition has non-default bound public.%I->public.%I',
            partition_row.child_name,
            partition_row.parent_name
          )
        );
      ELSE
        found_defaults := pg_catalog.array_append(
          found_defaults,
          partition_row.child_name
        );
      END IF;
      CONTINUE;
    END IF;

    IF partition_row.parent_name NOT IN ({runtime_parents}) THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'unexpected runtime partition public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    name_parts := pg_catalog.regexp_match(
      partition_row.child_name,
      '^' || partition_row.parent_name || '_([0-9]{{4}})_(0[1-9]|1[0-2])$'
    );
    IF name_parts IS NULL THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'unexpected runtime partition public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    bound_parts := pg_catalog.regexp_match(
      partition_row.partition_bound,
      '^FOR VALUES FROM \\(''([^'']+)''\\) TO \\(''([^'']+)''\\)$'
    );
    IF bound_parts IS NULL THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'non-canonical monthly bound public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END IF;
    BEGIN
      actual_start := bound_parts[1]::timestamptz;
      actual_end := bound_parts[2]::timestamptz;
    EXCEPTION WHEN OTHERS THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'unparseable monthly bound public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
      CONTINUE;
    END;
    expected_start := pg_catalog.make_timestamptz(
      name_parts[1]::integer,
      name_parts[2]::integer,
      1,
      0,
      0,
      0,
      'UTC'
    );
    expected_end := expected_start + INTERVAL '1 month';
    IF actual_start <> expected_start OR actual_end <> expected_end THEN
      problems := pg_catalog.array_append(
        problems,
        pg_catalog.format(
          'wrong calendar-month bound public.%I->public.%I',
          partition_row.child_name,
          partition_row.parent_name
        )
      );
    END IF;
  END LOOP;

  SELECT pg_catalog.array_cat(
      problems,
      COALESCE(
        pg_catalog.array_agg(
          'missing frozen default partition public.' || default_name
          ORDER BY default_name
        ),
        ARRAY[]::text[]
      )
    )
    INTO problems
    FROM pg_catalog.jsonb_each_text(defaults) AS expected(parent_name, default_name)
    WHERE NOT default_name = ANY(found_defaults);

  WITH partition_layout AS (
{_indent_sql(PUBLIC_PARTITION_LAYOUT_SQL.rstrip(), 4)}
  ),
  monthly_bounds AS (
    SELECT
      parent_name,
      child_name,
      child_oid,
      (pg_catalog.regexp_match(
        partition_bound,
        '^FOR VALUES FROM \\(''([^'']+)''\\) TO \\(''([^'']+)''\\)$'
      ))[1]::timestamptz AS bound_start,
      (pg_catalog.regexp_match(
        partition_bound,
        '^FOR VALUES FROM \\(''([^'']+)''\\) TO \\(''([^'']+)''\\)$'
      ))[2]::timestamptz AS bound_end
    FROM partition_layout
    WHERE parent_schema = 'public'
      AND child_schema = 'public'
      AND parent_relkind = 'p'
      AND child_relkind = 'r'
      AND child_is_partition
      AND parent_name IN ({runtime_parents})
      AND child_name ~ (
        '^' || parent_name || '_[0-9]{{4}}_(0[1-9]|1[0-2])$'
      )
      AND partition_bound ~
        '^FOR VALUES FROM \\(''[^'']+''\\) TO \\(''[^'']+''\\)$'
  )
  SELECT COALESCE(
      pg_catalog.array_agg(
        pg_catalog.format(
          'overlapping monthly partitions public.%I and public.%I->public.%I',
          left_partition.child_name,
          right_partition.child_name,
          left_partition.parent_name
        )
        ORDER BY left_partition.parent_name,
                 left_partition.child_name,
                 right_partition.child_name
      ),
      ARRAY[]::text[]
    )
    INTO overlap_problems
    FROM monthly_bounds AS left_partition
    JOIN monthly_bounds AS right_partition
      ON right_partition.parent_name = left_partition.parent_name
     AND right_partition.child_oid > left_partition.child_oid
     AND pg_catalog.tstzrange(
           left_partition.bound_start,
           left_partition.bound_end,
           '[)'
         ) && pg_catalog.tstzrange(
           right_partition.bound_start,
           right_partition.bound_end,
           '[)'
         );
  problems := pg_catalog.array_cat(problems, overlap_problems);

  IF pg_catalog.cardinality(problems) > 0 THEN
    RAISE EXCEPTION
      'safety-first public partition layout drift: %',
      pg_catalog.array_to_string(problems, '; ');
  END IF;
END
$baseline_partition_guard$;
""".strip()


def render_psql_platform_guard() -> str:
    """Render the complete host bootstrap guard from the shared contract."""

    return "\n".join(
        (
            render_psql_fresh_target_guard(),
            render_psql_catalog_guard(),
            render_psql_partition_guard(),
            render_psql_cluster_ownership_commit(),
        )
    )


def _indent_sql(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" for line in value.splitlines())


if __name__ == "__main__":
    command = sys.argv[1:]
    if command == ["--render-psql-guard"]:
        print(render_psql_catalog_guard())
    elif command == ["--render-platform-psql-guard"]:
        print(render_psql_platform_guard())
    else:
        raise SystemExit(
            f"usage: {sys.argv[0]} {{--render-psql-guard|--render-platform-psql-guard}}"
        )
