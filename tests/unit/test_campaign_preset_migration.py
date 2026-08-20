from __future__ import annotations

import importlib

import pytest

from migrations.revision_guard import load_project_revision_chain


def test_campaign_preset_migration_is_the_single_forward_only_head() -> None:
    migration = importlib.import_module("migrations.versions.0006_campaign_preset_snapshot")
    chain = load_project_revision_chain()

    assert migration.down_revision == "0005_am_columns_setting"
    # Голова уехала на 0009: у каждого фонового воркера появился durable heartbeat.
    # Цепочка остаётся линейной и forward-only.
    assert chain.head == "0009_worker_heartbeats"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()

    bid_migration = importlib.import_module("migrations.versions.0007_preset_bid_and_link")
    assert bid_migration.down_revision == "0006_campaign_preset_snapshot"
    with pytest.raises(RuntimeError, match="forward-only"):
        bid_migration.downgrade()

    status_migration = importlib.import_module("migrations.versions.0008_account_status_evidence")
    assert status_migration.down_revision == "0007_preset_bid_and_link"
    with pytest.raises(RuntimeError, match="forward-only"):
        status_migration.downgrade()

    heartbeat_migration = importlib.import_module("migrations.versions.0009_worker_heartbeats")
    assert heartbeat_migration.down_revision == "0008_account_status_evidence"
    with pytest.raises(RuntimeError, match="forward-only"):
        heartbeat_migration.downgrade()


def test_account_status_migration_adds_evidence_without_assuming_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Существующим строкам нельзя дописать «активен»: это была бы догадка."""

    migration = importlib.import_module("migrations.versions.0008_account_status_evidence")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert "ADD COLUMN account_status smallint" in sql
    assert "ADD COLUMN account_status_observed_at timestamp with time zone" in sql
    assert "DEFAULT" not in sql
    assert "UPDATE" not in sql
    assert "DROP COLUMN" not in sql


def test_worker_heartbeat_migration_creates_an_empty_table_without_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Таблица стартует пустой: у существующих (несуществующих) строк нет
    heartbeat, и подставлять его значило бы придумать данные (issue #176)."""

    migration = importlib.import_module("migrations.versions.0009_worker_heartbeats")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert "CREATE TABLE public.worker_heartbeats" in sql
    assert "worker_name text PRIMARY KEY" in sql
    assert "last_heartbeat_at timestamp with time zone NOT NULL" in sql
    assert "last_poll_success_at timestamp with time zone" in sql
    assert "DEFAULT" not in sql
    assert "UPDATE" not in sql
    assert "DROP" not in sql


def test_campaign_preset_migration_adds_snapshot_fields_and_purchase_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module("migrations.versions.0006_campaign_preset_snapshot")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    for column in (
        "countries",
        "age_min",
        "age_max",
        "genders",
        "placements",
        "budget_level",
        "daily_budget",
    ):
        assert f"ADD COLUMN {column}" in sql
    assert "custom_event_type = 'PURCHASE'" in sql
    assert "ck_campaign_preset_purchase_only" in sql
    assert "DROP COLUMN" not in sql
