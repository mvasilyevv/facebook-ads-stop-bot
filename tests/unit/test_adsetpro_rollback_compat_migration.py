from __future__ import annotations

import importlib

from sqlalchemy import CheckConstraint

from core.models.trackers.adsetpro_postback import (
    ADSETPRO_TRANSITION_EVENT_TYPES,
    AdsetProPostbackEvent,
)


def test_transition_constraint_matches_migration_and_model() -> None:
    migration = importlib.import_module("migrations.versions.0035_adsetpro_rollback_compat")

    assert migration.down_revision == "0034_event_driven_tracker"
    assert len(migration.revision) <= 32
    assert migration.TRANSITION_EVENT_TYPES == ADSETPRO_TRANSITION_EVENT_TYPES
    assert {"hold", "accept", "cpa_accept", "redep", "baddep"}.issubset(
        ADSETPRO_TRANSITION_EVENT_TYPES
    )

    constraint = next(
        item
        for item in AdsetProPostbackEvent.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_adsetpro_postback_events_adsetpro_event_type"
    )
    constraint_sql = str(constraint.sqltext)
    assert "lower(trim(event_type))" in constraint_sql
    for event_type in ADSETPRO_TRANSITION_EVENT_TYPES:
        assert repr(event_type) in constraint_sql


def test_upgrade_replaces_and_validates_transition_constraint(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0035_adsetpro_rollback_compat")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert "DROP CONSTRAINT IF EXISTS" in sql
    assert "NOT VALID" in sql
    assert "VALIDATE CONSTRAINT" in sql
    for event_type in ADSETPRO_TRANSITION_EVENT_TYPES:
        assert f"'{event_type}'" in sql


def test_downgrade_refuses_to_hide_unmigrated_alias_rows(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0035_adsetpro_rollback_compat")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()

    sql = "\n".join(statements)
    assert "event_type IN ('registration', 'ftd', 'redeposit')" in sql
    assert "VALIDATE CONSTRAINT" in sql
    assert "'hold'" not in sql
