# -*- coding: utf-8 -*-
"""Contracts for presentation-only Ads Manager columns settings."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.settings_observer import AdsManagerColumnsPatchRequest
from core.models.settings.observer_config import ObserverConfig
from core.observer.am_columns import (
    DEFAULT_AM_COLUMNS,
    DEFAULT_AM_COLUMNS_QS,
    KNOWN_AM_COLUMN_OPTIONS,
    build_am_columns_qs,
    selected_am_columns,
)

ROOT = Path(__file__).resolve().parents[2]


def test_backend_known_columns_are_derived_from_browser_agent_default() -> None:
    source = (ROOT / "services/browser-agent/src/am/am-columns-preset.ts").read_text(
        encoding="utf-8"
    )
    declaration = re.search(
        r"export const DEFAULT_COLUMNS_QS =(?P<body>.*?);\n\nconst ALLOWED_COLUMNS_QUERY_KEYS",
        source,
        re.DOTALL,
    )
    assert declaration is not None
    browser_default = "".join(re.findall(r'"([^"\n]*)"', declaration.group("body")))

    assert browser_default == DEFAULT_AM_COLUMNS_QS
    assert tuple(column_id for column_id, _label in KNOWN_AM_COLUMN_OPTIONS) == DEFAULT_AM_COLUMNS


def test_empty_selection_uses_default_instead_of_storing_empty_query() -> None:
    assert build_am_columns_qs(None) is None
    assert build_am_columns_qs([]) is None
    assert selected_am_columns(None) == DEFAULT_AM_COLUMNS
    assert selected_am_columns("  ") == DEFAULT_AM_COLUMNS


def test_server_builds_query_only_from_known_checkbox_ids() -> None:
    query = build_am_columns_qs(["name", "spend", "name"])

    assert query is not None
    parsed = parse_qs(query)
    assert parsed["columns"] == ["name,spend"]
    assert parsed["attribution_windows"] == ["default"]
    assert parsed["column_preset"] == ["1030561339462971"]
    assert "access_token" not in parsed


def test_api_rejects_unknown_column_instead_of_persisting_raw_query() -> None:
    with pytest.raises(ValidationError, match="Неизвестные колонки Ads Manager"):
        AdsManagerColumnsPatchRequest(column_ids=["name", "access_token=secret"])


def test_model_and_forward_only_migration_keep_nullable_fallback() -> None:
    column = ObserverConfig.__table__.columns["am_columns_qs"]
    migration = importlib.import_module("migrations.versions.0005_am_columns_setting")

    assert column.nullable is True
    assert migration.down_revision == "0004_vision_token_self_heal"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
