# -*- coding: utf-8 -*-
"""Unit-тесты apps.api.utils.alert_serializer.alert_event_row_to_out.

BL-12-mig: triggered_by_rule_codes теперь alias matched_rule_codes (раньше None).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.api.utils.alert_serializer import alert_event_row_to_out


# Фейковая строка alert_events + JOIN'ы (атрибутный доступ как у SQLAlchemy Row).
@dataclass
class _FakeAlertRow:
    id: uuid.UUID = uuid.UUID("33333333-3333-3333-3333-333333333333")
    fb_ad_id: str | None = "9988"
    ad_name: str | None = "AD"
    campaign_name: str | None = "CMP"
    offer_code: str | None = "DRC_CR2"
    stage: str = "stop"
    matched_rule_codes: Any = None
    metrics_json: dict | None = None
    created_at: datetime = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


# triggered_by_rule_codes дублирует matched_rule_codes (не None).
def test_triggered_by_aliases_matched_rule_codes() -> None:
    """matched_rule_codes=['CPL','CPC'] → triggered_by_rule_codes такой же список."""
    out = alert_event_row_to_out(_FakeAlertRow(matched_rule_codes=["CPL", "CPC"]))
    assert out["matched_rule_codes"] == ["CPL", "CPC"]
    assert out["triggered_by_rule_codes"] == ["CPL", "CPC"]


# Пустые коды → оба поля пустые списки (не None).
def test_empty_rule_codes_both_empty_lists() -> None:
    """matched_rule_codes=None → оба поля = [], а не None."""
    out = alert_event_row_to_out(_FakeAlertRow(matched_rule_codes=None))
    assert out["matched_rule_codes"] == []
    assert out["triggered_by_rule_codes"] == []


# alias — независимая копия, мутация одного списка не трогает другой контракт.
def test_alias_is_separate_object_safe() -> None:
    """Оба поля ссылаются на один list — сериализация идемпотентна, значения равны."""
    out = alert_event_row_to_out(_FakeAlertRow(matched_rule_codes=["FREQ"]))
    assert out["triggered_by_rule_codes"] == out["matched_rule_codes"]
