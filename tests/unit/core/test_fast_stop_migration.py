from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy.dialects import postgresql


def _load_fast_stop_migration():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "versions"
        / "20260323_0006_fast_stop_pipeline.py"
    )
    spec = importlib.util.spec_from_file_location("fast_stop_migration", migration_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Не удалось загрузить модуль fast-stop миграции")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Проверяет, что enum-типы fast-stop миграции не пытаются создаваться повторно на DDL колонок.
def test_fast_stop_migration_uses_postgres_enums_without_auto_create() -> None:
    migration = _load_fast_stop_migration()

    assert isinstance(migration._RISK_BAND_ENUM, postgresql.ENUM)
    assert migration._RISK_BAND_ENUM.create_type is False

    assert isinstance(migration._SCAN_PIPELINE_KIND_ENUM, postgresql.ENUM)
    assert migration._SCAN_PIPELINE_KIND_ENUM.create_type is False

    assert isinstance(migration._ACTION_JOB_STATUS_ENUM, postgresql.ENUM)
    assert migration._ACTION_JOB_STATUS_ENUM.create_type is False

    assert isinstance(migration._ACTION_TYPE_ENUM, postgresql.ENUM)
    assert migration._ACTION_TYPE_ENUM.create_type is False
