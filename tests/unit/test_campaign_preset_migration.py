from __future__ import annotations

import importlib

import pytest

from migrations.revision_guard import load_project_revision_chain


def test_campaign_preset_migration_is_the_single_forward_only_head() -> None:
    migration = importlib.import_module("migrations.versions.0006_campaign_preset_snapshot")
    chain = load_project_revision_chain()

    assert migration.down_revision == "0005_am_columns_setting"
    # Голова уехала на 0011: у задачи появился факт ручной сверки (#360).
    # Цепочка остаётся линейной и forward-only.
    assert chain.head == "0011_task_manual_review"
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


def test_manual_review_migration_adds_facts_without_touching_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ручная сверка — новая ось, а не переписывание исхода (issue #360).

    Ни одна существующая строка не должна поменяться: у неё нет сверки, и
    придумать её задним числом нельзя. Наблюдение — закрытый список из трёх
    значений: кнопки «ок» здесь нет по замыслу.
    """

    migration = importlib.import_module("migrations.versions.0011_task_manual_review")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert migration.down_revision == "0010_offer_rule_thresholds"
    for column in (
        "manual_review_observation",
        "manual_review_at",
        "manual_review_by",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
    assert "IN ('stopped', 'active', 'missing')" in sql
    assert "ck_task_queue_manual_review_complete" in sql
    assert "UPDATE" not in sql
    assert "DROP COLUMN" not in sql
    assert "result" not in sql.replace("result->>'outcome'", "")
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


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
