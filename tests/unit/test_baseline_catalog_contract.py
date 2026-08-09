from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql

from core.models import Base
from migrations import baseline_contract

ROOT = Path(__file__).resolve().parents[2]


def _row(kind: str, identity: str, definition: str) -> dict[str, str]:
    return {
        "artifact_kind": kind,
        "artifact_identity": identity,
        "normalized_definition": definition,
    }


def _frozen_check_constraint_identities() -> set[str]:
    asset = (ROOT / "migrations/versions/0001_safety_first_baseline.sql").read_text(
        encoding="utf-8"
    )
    identities: set[str] = set()
    for table_name, table_body in re.findall(
        r"CREATE TABLE public\.([a-z0-9_]+) \((.*?)\n\)(?:;|\nPARTITION BY)",
        asset,
        re.DOTALL,
    ):
        for constraint_name in re.findall(
            r"\bCONSTRAINT ([a-z0-9_]+) CHECK \(",
            table_body,
        ):
            identity = f"check_constraint:public.{table_name}.{constraint_name}"
            assert identity not in identities
            identities.add(identity)
    for table_name, constraint_name in re.findall(
        r"ALTER TABLE(?: ONLY)? public\.([a-z0-9_]+)\n"
        r"    ADD CONSTRAINT ([a-z0-9_]+) CHECK \(",
        asset,
    ):
        identity = f"check_constraint:public.{table_name}.{constraint_name}"
        assert identity not in identities
        identities.add(identity)
    return identities


def test_reviewed_manifest_covers_exact_runtime_catalog_surface() -> None:
    check_constraints = _frozen_check_constraint_identities()
    kinds = Counter(
        key.split(":", maxsplit=1)[0] for key in baseline_contract.BASELINE_ARTIFACT_HASHES
    )

    assert kinds == {
        "extension": 2,
        "function": 5,
        "trigger": 12,
        "view": 1,
        "check_constraint": len(check_constraints),
    }
    assert (
        set(baseline_contract.BASELINE_ARTIFACT_HASHES)
        == {
            "extension:pg_catalog.plpgsql",
            "extension:public.pgcrypto",
            "function:public.bump_fb_operator_revision(event_scope text, event_id text)",
            "function:public.enforce_adset_duplicate_preview_consume_once()",
            "function:public.invalidate_browser_readiness_on_maintenance()",
            "function:public.notify_fb_operator_event()",
            "function:public.notify_fb_operator_statement()",
            "trigger:public.ad_metrics.trg_ad_metrics_operator_revision",
            "trigger:public.adset_duplicate_previews.trg_adset_duplicate_previews_consume_once",
            "trigger:public.cabinet_runtime.trg_cabinet_runtime_operator_notify",
            "trigger:public.campaign_run.trg_campaign_run_operator_notify",
            "trigger:public.fb_ads.trg_fb_ads_operator_revision",
            "trigger:public.incidents.trg_incidents_operator_notify",
            "trigger:public.notification_deliveries.trg_notification_deliveries_operator_notify",
            "trigger:public.observer_config.trg_observer_config_operator_revision",
            "trigger:public.scan_runs.trg_scan_runs_operator_revision",
            "trigger:public.system_config.trg_system_config_browser_maintenance_readiness",
            "trigger:public.task_queue.trg_task_queue_operator_notify",
            "trigger:public.tracker_click_state.trg_tracker_click_state_operator_revision",
            "view:public.operator_revision_state",
        }
        | check_constraints
    )


def test_frozen_sql_and_orm_checks_are_all_in_the_catalog_manifest() -> None:
    frozen = _frozen_check_constraint_identities()
    identifier_preparer = postgresql.dialect().identifier_preparer
    orm = {
        (
            "check_constraint:public."
            f"{table.name}.{identifier_preparer.format_constraint(constraint)}"
        )
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }

    assert len(frozen) == 97
    assert orm <= frozen
    assert {
        key
        for key in baseline_contract.BASELINE_ARTIFACT_HASHES
        if key.startswith("check_constraint:")
    } == frozen


def test_catalog_artifact_drift_reports_missing_unexpected_and_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_definition = "CREATE VIEW public.expected AS SELECT 1;"
    changed_definition = "CREATE VIEW public.changed AS SELECT 2;"
    unexpected_definition = "CREATE VIEW public.legacy AS SELECT 3;"
    monkeypatch.setattr(
        baseline_contract,
        "BASELINE_ARTIFACT_HASHES",
        {
            "view:public.expected": baseline_contract.definition_sha256(expected_definition),
            "view:public.changed": baseline_contract.definition_sha256(expected_definition),
            "function:public.missing()": baseline_contract.definition_sha256(
                "CREATE FUNCTION public.missing() RETURNS void"
            ),
        },
    )

    drift = baseline_contract.catalog_artifact_drift(
        [
            _row("view", "public.expected", expected_definition),
            _row("view", "public.changed", changed_definition),
            _row("view", "public.legacy", unexpected_definition),
        ]
    )

    assert drift == [
        "missing function:public.missing()",
        "unexpected view:public.legacy",
        "definition changed view:public.changed",
    ]


def test_catalog_artifact_contract_rejects_duplicate_identity() -> None:
    rows = [
        _row("view", "public.operator_revision_state", "SELECT 1"),
        _row("view", "public.operator_revision_state", "SELECT 1"),
    ]

    with pytest.raises(
        RuntimeError,
        match="duplicate baseline catalog artifact",
    ):
        baseline_contract.catalog_artifact_hashes(rows)


def test_catalog_query_captures_check_expression_and_enforcement_state() -> None:
    query = baseline_contract.CATALOG_ARTIFACTS_SQL

    assert "constraint_record.contype = 'c'" in query
    assert "pg_catalog.pg_get_constraintdef" in query
    assert "constraint_record.convalidated" in query
    assert "constraint_record.connoinherit" in query
    assert "'check_constraint'::text AS artifact_kind" in query
    assert "extension.extversion" in query
    assert "extension.extrelocatable" in query
    assert (
        "WHERE namespace.nspname = 'public'"
        not in query.split("database_extensions AS (", maxsplit=1)[1].split("),", maxsplit=1)[0]
    )


def test_standalone_catalog_guard_uses_postgresql_ownership_dependencies() -> None:
    query = baseline_contract.PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL

    assert "namespace.nspname = 'public'" in query
    assert "'pg_catalog.pg_type'::pg_catalog.regclass" in query
    assert "'pg_catalog.pg_collation'::pg_catalog.regclass" in query
    for catalog in (
        "pg_proc",
        "pg_operator",
        "pg_conversion",
        "pg_ts_config",
        "pg_ts_dict",
        "pg_ts_parser",
        "pg_ts_template",
        "pg_opclass",
        "pg_opfamily",
        "pg_default_acl",
    ):
        assert f"pg_catalog.{catalog}" in query
    assert "relation.relkind = 'c'" in query
    assert "namespace_security" in query
    # Normal dependencies belong to the standalone object. Internal, automatic
    # and extension dependencies prove that another catalog object owns it.
    assert query.count("dependency.deptype <> 'n'") == 2
    assert baseline_contract.describe_standalone_public_catalog_objects(
        [
            {
                "object_kind": "type",
                "object_name": "legacy_state",
                "object_detail": "enum",
            },
            {
                "object_kind": "collation",
                "object_name": "legacy_collation",
                "object_detail": "libc",
            },
        ]
    ) == [
        "type:public.legacy_state(enum)",
        "collation:public.legacy_collation(libc)",
    ]


def test_installed_standalone_guard_allows_only_manifested_routines() -> None:
    rows = [
        {
            "object_kind": "function",
            "object_name": "notify_fb_operator_event()",
            "object_detail": "function",
        },
        {
            "object_kind": "function",
            "object_name": "unreviewed()",
            "object_detail": "function",
        },
        {
            "object_kind": "operator",
            "object_name": "legacy(integer,integer)",
            "object_detail": "binary",
        },
    ]

    assert baseline_contract.describe_standalone_public_catalog_objects(
        rows,
        allow_manifested_routines=True,
    ) == [
        "function:public.unreviewed()(function)",
        "operator:public.legacy(integer,integer)(binary)",
    ]


def _extension_row(
    schema: str,
    name: str,
    version: str,
    relocatable: bool,
) -> dict[str, object]:
    return {
        "extension_schema": schema,
        "extension_name": name,
        "extension_version": version,
        "extension_relocatable": relocatable,
    }


def test_extension_layout_is_exact_before_and_after_baseline() -> None:
    plpgsql = _extension_row("pg_catalog", "plpgsql", "1.0", False)
    pgcrypto = _extension_row("public", "pgcrypto", "1.3", True)

    baseline_contract.validate_database_extension_layout(
        [plpgsql],
        baseline_installed=False,
    )
    baseline_contract.validate_database_extension_layout(
        [plpgsql, pgcrypto],
        baseline_installed=True,
    )
    with pytest.raises(RuntimeError, match="unexpected extension public.hstore"):
        baseline_contract.validate_database_extension_layout(
            [plpgsql, pgcrypto, _extension_row("public", "hstore", "1.8", True)],
            baseline_installed=True,
        )
    with pytest.raises(RuntimeError, match="definition changed public.pgcrypto"):
        baseline_contract.validate_database_extension_layout(
            [plpgsql, _extension_row("public", "pgcrypto", "1.2", True)],
            baseline_installed=True,
        )


def _partition_row(
    parent: str,
    child: str,
    bound: str,
    **overrides: Any,
) -> dict[str, object]:
    row: dict[str, object] = {
        "parent_schema": "public",
        "parent_name": parent,
        "parent_relkind": "p",
        "parent_oid": 1,
        "child_schema": "public",
        "child_name": child,
        "child_relkind": "r",
        "child_oid": 2,
        "child_is_partition": True,
        "partition_bound": bound,
    }
    row.update(overrides)
    return row


def _default_partition_rows() -> list[dict[str, object]]:
    return [
        _partition_row(parent, child, "DEFAULT")
        for parent, child in baseline_contract.BASELINE_DEFAULT_PARTITIONS.items()
    ]


def test_partition_layout_accepts_only_frozen_defaults_and_canonical_utc_months() -> None:
    rows = _default_partition_rows()
    rows.extend(
        [
            _partition_row(
                "ad_metrics",
                "ad_metrics_2026_07",
                "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
                child_oid=10,
            ),
            _partition_row(
                "meta_api_audit_log",
                "meta_api_audit_log_2026_08",
                "FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00')",
                child_oid=11,
            ),
        ]
    )

    hidden = baseline_contract.validate_public_partition_layout(
        rows,
        require_baseline_defaults=True,
    )

    assert hidden == frozenset(str(row["child_name"]) for row in rows)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            _partition_row(
                "ad_metrics",
                "ad_metrics_2026_07",
                "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
                child_schema="shadow",
            ),
            "cross-schema",
        ),
        (
            _partition_row("ad_metrics", "ad_metrics_bad", "DEFAULT"),
            "unexpected runtime partition",
        ),
        (
            _partition_row(
                "ad_metrics",
                "ad_metrics_2026_07",
                "FOR VALUES FROM ('2026-07-02 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
            ),
            "wrong calendar-month bound",
        ),
        (
            _partition_row(
                "ad_metrics",
                "ad_metrics_2026_07",
                "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
                child_relkind="p",
            ),
            "nested partition child",
        ),
        (
            _partition_row(
                "ad_metrics",
                "ad_metrics_2026_07",
                "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
                parent_relkind="r",
            ),
            "non-partitioned parent inheritance",
        ),
    ],
)
def test_partition_layout_rejects_noncanonical_catalog_rows(
    row: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        baseline_contract.validate_public_partition_layout(
            [*_default_partition_rows(), row],
            require_baseline_defaults=True,
        )


def test_partition_layout_rejects_missing_default_and_overlaps() -> None:
    with pytest.raises(RuntimeError, match="missing frozen default.*ad_metrics_default"):
        baseline_contract.validate_public_partition_layout(
            [row for row in _default_partition_rows() if row["child_name"] != "ad_metrics_default"],
            require_baseline_defaults=True,
        )

    duplicate_month = _partition_row(
        "ad_metrics",
        "ad_metrics_2026_07",
        "FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00')",
    )
    with pytest.raises(RuntimeError, match="overlapping monthly partition"):
        baseline_contract.validate_public_partition_layout(
            [*_default_partition_rows(), duplicate_month, dict(duplicate_month)],
            require_baseline_defaults=True,
        )


def test_rendered_psql_guard_embeds_exact_manifest_and_revision() -> None:
    guard = baseline_contract.render_psql_catalog_guard()

    assert baseline_contract.BASELINE_REVISION in guard
    assert "pg_catalog.pg_get_functiondef" in guard
    assert "pg_catalog.pg_get_triggerdef" in guard
    assert "pg_catalog.pg_get_viewdef" in guard
    assert "pg_catalog.pg_get_constraintdef" in guard
    assert "definition changed" in guard
    assert "unexpected " in guard
    assert "missing " in guard
    for digest in baseline_contract.BASELINE_ARTIFACT_HASHES.values():
        assert digest in guard


def test_rendered_platform_guard_is_the_complete_shared_contract() -> None:
    guard = baseline_contract.render_psql_platform_guard()

    assert "fresh_target_guard" in guard
    assert "baseline_catalog_guard" in guard
    assert "baseline_partition_guard" in guard
    assert "fb_agent.bootstrap_cluster_id" in guard
    assert "actual_cluster_id IS DISTINCT FROM expected_cluster_id" in guard
    assert "extension.extversion" in guard
    assert "overlapping monthly partitions" in guard
    assert r") \gexec" in guard
    assert r") \\gexec" not in guard
    assert guard.index("$baseline_partition_guard$;") < guard.index("ALTER DATABASE %I SET")
    for sentinel in baseline_contract.BASELINE_RELATION_SENTINELS:
        assert sentinel in guard


def test_every_fresh_baseline_entrypoint_uses_the_shared_catalog_contract() -> None:
    alembic_environment = (ROOT / "migrations/env.py").read_text(encoding="utf-8")
    baseline_revision = (ROOT / "migrations/versions/0001_safety_first_baseline.py").read_text(
        encoding="utf-8"
    )
    locked_migrator = (ROOT / "scripts/run-migrations-locked.py").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts/platform-bootstrap.sh").read_text(encoding="utf-8")

    for source in (alembic_environment, baseline_revision, locked_migrator):
        assert "migrations.baseline_contract" in source
        assert "assert_catalog_artifacts" in source
        assert "CATALOG_ARTIFACTS_SQL" in source
        assert "PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL" in source
        assert "describe_standalone_public_catalog_objects" in source
    assert "migrations/baseline_contract.py" in bootstrap
    assert "--render-platform-psql-guard" in bootstrap
    assert "DO $fresh_target_guard$" not in bootstrap
    assert "standalone_types AS" not in bootstrap
