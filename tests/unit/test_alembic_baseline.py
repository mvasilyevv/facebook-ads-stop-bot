# -*- coding: utf-8 -*-
"""Static contracts for the irreversible safety-first installation baseline."""

from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from apps.cleanup_worker.retention import get_default_policy
from core.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = PROJECT_ROOT / "migrations" / "versions"
REVISION_PATH = VERSIONS_DIR / "0001_safety_first_baseline.py"
ASSET_PATH = VERSIONS_DIR / "0001_safety_first_baseline.sql"
REVISION_MODULE = "migrations.versions.0001_safety_first_baseline"

DEFAULT_PARTITIONS = {
    "ad_metrics_default",
    "adsetpro_postback_events_default",
    "alert_events_default",
    "meta_api_audit_log_default",
    "scan_runs_default",
}
ACTIVE_TASK_TYPES = {
    "meta_api_mutation",
    "observer_scan",
    "campaign_create",
    "tracker_event_process",
}


def _asset() -> str:
    return ASSET_PATH.read_text(encoding="utf-8")


def test_revision_chain_is_exactly_one_baseline() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    revision_files = sorted(VERSIONS_DIR.glob("*.py"))

    assert [path.name for path in revision_files] == ["0001_safety_first_baseline.py"]
    assert script.get_bases() == ["0001_safety_first_baseline"]
    assert script.get_heads() == ["0001_safety_first_baseline"]
    assert script.get_revision("0001_safety_first_baseline").down_revision is None


def test_direct_alembic_cli_imports_project_migration_contracts() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    assert config.get_main_option("prepend_sys_path") == "."
    assert config.get_main_option("path_separator") == "os"


def test_baseline_is_frozen_checksum_verified_and_runtime_independent() -> None:
    migration = importlib.import_module(REVISION_MODULE)
    revision_source = REVISION_PATH.read_text(encoding="utf-8")
    asset_digest = hashlib.sha256(ASSET_PATH.read_bytes()).hexdigest()

    assert asset_digest == migration.BASELINE_ASSET_SHA256
    assert "from core.models" not in revision_source
    assert "Base.metadata" not in revision_source
    assert "create_all" not in revision_source
    assert "subprocess" not in revision_source
    assert "psql" not in revision_source.lower().replace("psql meta-command", "")
    assert "alembic stamp" not in revision_source


def test_asset_is_exact_current_orm_schema_plus_default_partitions() -> None:
    tables = set(re.findall(r"^CREATE TABLE public\.([a-z0-9_]+) \(", _asset(), re.MULTILINE))

    assert tables == set(Base.metadata.tables) | DEFAULT_PARTITIONS
    assert len(Base.metadata.tables) == 47
    assert len(tables) == 52


def test_asset_has_required_postgresql_objects_and_only_default_partitions() -> None:
    asset = _asset()

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;" in asset
    assert len(re.findall(r"^CREATE FUNCTION public\.", asset, re.MULTILINE)) == 5
    assert "CREATE VIEW public.operator_revision_state AS" in asset
    assert len(re.findall(r"^CREATE TRIGGER ", asset, re.MULTILINE)) == 12
    attached_defaults = set(
        re.findall(
            r"ATTACH PARTITION public\.([a-z0-9_]+_default) DEFAULT;",
            asset,
        )
    )
    assert attached_defaults == DEFAULT_PARTITIONS
    assert not re.search(r"_20\d{2}_\d{2}\b", asset)


def test_asset_contains_only_active_task_types_and_no_contract_fallbacks() -> None:
    asset = _asset()
    constraint = re.search(
        r"CONSTRAINT ck_task_queue_task_type CHECK \((.+?)\)\n",
        asset,
    )
    assert constraint is not None
    constraint_types = set(re.findall(r"'([a-z_]+)'::character varying", constraint.group(1)))

    assert constraint_types == ACTIVE_TASK_TYPES
    for forbidden in (
        "cabinet_day_archives",
        "CREATE TABLE public.tracker_postback ",
        "CREATE TABLE public.tracker_postback_default ",
        "tracker_postback_id_seq",
        "ix_tracker_postback_",
        "fk_tracker_postback_",
        "creator_plans",
        "telegram_message_refs",
        "task_queue_n_minus_one_defaults",
        "poller_offset",
        "poller_heartbeat_at",
        "task_type='disable'",
        "task_type='enable'",
        "'disable'::character varying",
        "'enable'::character varying",
        "ad_deposit_corrections",
        "meta_activity_event",
        "meta_api_observation",
        "meta_api_webhook_event",
        "meta_delivery_diagnostic",
        "meta_insights_reconciliation",
        "offer_rule_stats",
        "message_thread_id",
        "'draft'::character varying",
        "ix_task_queue_draft",
        "ad_library",
        "CREATE TABLE public.tracker_aggregate ",
        "ix_tracker_agg_",
    ):
        assert forbidden not in asset


def test_adsetpro_inbox_accepts_only_canonical_event_types() -> None:
    asset = _asset()
    constraint = re.search(
        r"CONSTRAINT ck_adsetpro_postback_events_adsetpro_event_type CHECK \((.+?)\)\n",
        asset,
    )
    assert constraint is not None
    assert set(re.findall(r"'([a-z_]+)'::text", constraint.group(1))) == {
        "registration",
        "ftd",
        "redeposit",
    }


def test_fresh_offer_rules_do_not_recreate_retired_threshold_columns() -> None:
    asset = _asset()
    model = (PROJECT_ROOT / "core/models/catalog/offer_rule.py").read_text(encoding="utf-8")

    for retired in (
        "spend_no_event_threshold",
        "cpm_threshold",
        "ctr_threshold",
        "funnel_ratio_threshold",
    ):
        assert retired not in asset
        assert retired not in model


def test_fresh_offer_rules_enforce_safe_numeric_ranges_in_postgres() -> None:
    asset = _asset()
    expected = {
        "ck_offer_rules_cpa_threshold_positive_finite",
        "ck_offer_rules_frequency_threshold_positive_finite",
        "ck_offer_rules_stop_percent_range",
        "ck_offer_rules_warning_percent_range",
    }
    orm_constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["offer_rules"].constraints
        if constraint.name is not None
    }

    assert expected <= orm_constraint_names
    for constraint_name in expected:
        assert f"ADD CONSTRAINT {constraint_name} CHECK" in asset


def test_fresh_baseline_has_one_task_scheduler_and_no_write_only_projection() -> None:
    asset = _asset()
    task_table = re.search(
        r"CREATE TABLE public\.task_queue \((.*?)\n\);",
        asset,
        re.DOTALL,
    )
    assert task_table is not None
    assert "available_at timestamp with time zone" in task_table.group(1)
    assert "next_retry_at" not in task_table.group(1)

    assert "tracker_aggregate" not in asset
    assert "tracker_aggregate" not in Base.metadata.tables
    # AdSet.pro inbox retry metadata is intentionally independent from task scheduling.
    assert "next_retry_at" in Base.metadata.tables["adsetpro_postback_events"].columns


def test_fresh_baseline_keeps_only_live_operator_configuration_fields() -> None:
    asset = _asset()
    observer_table = re.search(
        r"CREATE TABLE public\.observer_config \((.*?)\n\);",
        asset,
        re.DOTALL,
    )
    account_table = re.search(
        r"CREATE TABLE public\.meta_account_snapshot \((.*?)\n\);",
        asset,
        re.DOTALL,
    )
    assert observer_table is not None
    assert account_table is not None

    for retired in (
        "jitter_seconds",
        "stale_data_threshold_seconds",
        "install_cost_usd",
        "agent_commission_percent",
        "creative_hash",
    ):
        assert retired not in asset
    assert set(Base.metadata.tables["meta_account_snapshot"].columns.keys()) == {
        "account_id",
        "timezone_name",
        "currency",
        "currency_observed_at",
        "created_at",
        "updated_at",
    }


def test_baseline_retention_seed_equals_current_policy() -> None:
    migration = importlib.import_module(REVISION_MODULE)

    assert migration.BASELINE_RETENTION_POLICY == get_default_policy()
    assert len(migration.BASELINE_RETENTION_POLICY) == 19


def test_sql_splitter_preserves_functions_literals_and_nested_comments() -> None:
    migration = importlib.import_module(REVISION_MODULE)
    sql = """
    -- leading ; comment
    CREATE FUNCTION public.example() RETURNS text AS $body$
    BEGIN
      /* outer ; /* inner ; */ still outer */
      RETURN 'one;two';
    END;
    $body$ LANGUAGE plpgsql;
    INSERT INTO public.example_table (value) VALUES ('it''s; fine');
    SELECT "semi;colon" FROM public.example_table;
    """

    statements = migration._split_sql(sql)

    assert len(statements) == 3
    assert "RETURN 'one;two';" in statements[0]
    assert "it''s; fine" in statements[1]
    assert '"semi;colon"' in statements[2]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'unterminated",
        'SELECT "unterminated',
        "SELECT $tag$unterminated",
        "SELECT 1 /* unterminated",
    ],
)
def test_sql_splitter_rejects_unterminated_construct(sql: str) -> None:
    migration = importlib.import_module(REVISION_MODULE)

    with pytest.raises(ValueError, match="unterminated SQL construct"):
        migration._split_sql(sql)


def test_asset_loader_rejects_psql_meta_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    migration = importlib.import_module(REVISION_MODULE)
    asset = tmp_path / "baseline.sql"
    asset.write_text("\\restrict token\nSELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(migration, "_asset_path", lambda: asset)
    monkeypatch.setattr(
        migration,
        "BASELINE_ASSET_SHA256",
        hashlib.sha256(asset.read_bytes()).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="psql meta-command"):
        migration._load_verified_asset()


def test_baseline_downgrade_is_fail_closed() -> None:
    migration = importlib.import_module(REVISION_MODULE)

    with pytest.raises(RuntimeError, match="irreversible"):
        migration.downgrade()


def test_revision_guard_rejects_standalone_type_before_baseline_ddl() -> None:
    migration = importlib.import_module(REVISION_MODULE)

    class _Rows:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self._rows = rows

        def mappings(self) -> list[dict[str, str]]:
            return self._rows

    class _Connection:
        def execute(self, statement: object) -> _Rows:
            query = str(statement)
            if "pg_catalog.pg_extension" in query:
                return _Rows(
                    [
                        {
                            "extension_schema": "pg_catalog",
                            "extension_name": "plpgsql",
                            "extension_version": "1.0",
                            "extension_relocatable": False,
                        }
                    ]
                )
            if "standalone_types" in query:
                return _Rows(
                    [
                        {
                            "object_kind": "type",
                            "object_name": "legacy_state",
                            "object_detail": "enum",
                        }
                    ]
                )
            if "pg_catalog.pg_inherits" in query:
                return _Rows([])
            if "pg_catalog.pg_class" in query:
                return _Rows([])
            raise AssertionError(query)

    with pytest.raises(RuntimeError, match="standalone.*legacy_state"):
        migration._assert_empty_public_schema(_Connection())


def test_release_migrators_never_create_all_or_stamp() -> None:
    worker_entrypoint = (PROJECT_ROOT / "docker/worker-entrypoint.sh").read_text(encoding="utf-8")
    locked_migrator = (PROJECT_ROOT / "scripts/run-migrations-locked.py").read_text(
        encoding="utf-8"
    )

    assert "alembic stamp" not in worker_entrypoint
    assert "apply_schema.py" not in worker_entrypoint
    assert "exec python -m scripts.run-migrations-locked" in worker_entrypoint
    assert "worker-entrypoint.sh" not in locked_migrator
    assert '("upgrade", "head")' in locked_migrator
    assert '("check",)' in locked_migrator
    assert "apply_schema.py" not in locked_migrator
    assert "alembic stamp" not in locked_migrator
