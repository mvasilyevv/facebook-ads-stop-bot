from __future__ import annotations

import importlib

import pytest

from migrations.revision_guard import load_project_revision_chain


def test_campaign_preset_migration_is_the_single_forward_only_head() -> None:
    migration = importlib.import_module("migrations.versions.0005_campaign_preset_snapshot")
    chain = load_project_revision_chain()

    assert migration.down_revision == "0004_vision_token_self_heal"
    assert chain.head == "0005_campaign_preset_snapshot"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_campaign_preset_migration_adds_snapshot_fields_and_purchase_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module("migrations.versions.0005_campaign_preset_snapshot")
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
