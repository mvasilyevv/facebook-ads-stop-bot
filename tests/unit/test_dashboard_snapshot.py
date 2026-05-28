# -*- coding: utf-8 -*-
"""Unit-тесты pure-функций модуля core.dashboard.snapshot.

Проверяем маппинг ROW → dict без обращения к БД (через фейковую
namedtuple-обёртку).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from core.dashboard.snapshot import _build_metrics_dict, _build_row_dict, _build_sql


# Простой контейнер, имитирующий Row из SQLAlchemy (атрибутный доступ).
@dataclass
class _FakeRow:
    """Имитирует SQLAlchemy Row — поддерживает доступ через атрибуты."""

    fb_ad_id: str = "123456"
    internal_id: uuid.UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")
    ad_name: str = "AD"
    campaign_name: str | None = "CMP"
    adset_name: str | None = "ADS"
    offer_code: str | None = "DRC_CR2"
    offer_id: uuid.UUID | None = uuid.UUID("22222222-2222-2222-2222-222222222222")
    alert_state: str | None = "normal"
    snoozed_until: datetime | None = None
    open_state_token: uuid.UUID | None = None
    last_transition_at: datetime | None = None
    last_warning_at: datetime | None = None
    last_stop_at: datetime | None = None
    is_active: bool = True
    last_seen_at: datetime | None = None
    meta_ad_status: str | None = None
    m_cycle_ts: datetime | None = None
    m_spend: Decimal | None = None
    m_impressions: int | None = None
    m_clicks: int | None = None
    m_ctr: Decimal | None = None
    m_cpc: Decimal | None = None
    m_cpm: Decimal | None = None
    m_reach: int | None = None
    m_frequency: Decimal | None = None
    m_leads: int | None = None
    m_cost_per_lead: Decimal | None = None
    m_registrations: int | None = None
    m_cost_per_registration: Decimal | None = None
    m_deposits: int | None = None


# Базовый маппинг ORM-полей → dict с правильными ключами и форматом.
def test_build_row_dict_basic_mapping() -> None:
    """Маппинг ROW → dict: все обязательные поля присутствуют, типы корректны."""
    row = _FakeRow(
        fb_ad_id="999",
        ad_name="My Ad",
        campaign_name="CMP_X",
        offer_code="DRC_CR2",
        alert_state="warning_sent",
        is_active=True,
        last_seen_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    d = _build_row_dict(row)
    assert d["fb_ad_id"] == "999"
    assert d["ad_name"] == "My Ad"
    assert d["campaign_name"] == "CMP_X"
    assert d["offer_code"] == "DRC_CR2"
    assert d["alert_state"] == "warning_sent"
    assert d["is_active"] is True
    assert d["last_seen_at"] == "2026-01-01T12:00:00+00:00"
    # offer_id и internal_id — UUID → str
    assert isinstance(d["internal_id"], str)
    # meta_ad_status и metrics — None по умолчанию
    assert d["meta_ad_status"] is None
    assert d["metrics"] is None


# Отсутствие ad_metrics за окно → metrics=None (LATERAL вернул NULL'ы).
def test_metrics_none_when_no_latest_metric() -> None:
    """m_cycle_ts=None → весь блок metrics=None, не падает."""
    row = _FakeRow(m_cycle_ts=None)
    d = _build_row_dict(row)
    assert d["metrics"] is None


# Decimal сериализуется как str для JSON-стабильности.
def test_decimal_serialized_as_str() -> None:
    """Spend/CTR/CPC и т.д. — Decimal → str (точность сохранена)."""
    row = _FakeRow(
        m_cycle_ts=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        m_spend=Decimal("12.34"),
        m_clicks=50,
        m_ctr=Decimal("0.0500"),
        m_cpc=Decimal("0.2500"),
        m_impressions=1000,
        m_leads=5,
    )
    d = _build_row_dict(row)
    assert d["metrics"] is not None
    assert d["metrics"]["spend"] == "12.34"
    assert d["metrics"]["ctr"] == "0.0500"
    assert d["metrics"]["cpc"] == "0.2500"
    # int-поля — int (не str)
    assert d["metrics"]["clicks"] == 50
    assert d["metrics"]["impressions"] == 1000
    assert d["metrics"]["leads"] == 5
    # отсутствующие — None
    assert d["metrics"]["deposits"] is None


# Передача alert_states в _build_sql добавляет WHERE с COALESCE.
def test_sql_filter_alert_states_includes_where_clause() -> None:
    """alert_states фильтр генерирует COALESCE-WHERE и кладёт массив в params."""
    sql, params = _build_sql(
        fb_ad_ids=None,
        alert_states=["warning_sent", "stop_sent"],
        include_inactive=False,
        incidents_only=False,
        incident_stage=None,
        limit=200,
        offset=0,
    )
    assert "COALESCE(s.alert_state, 'normal') = ANY(:alert_states)" in sql
    assert params["alert_states"] == ["warning_sent", "stop_sent"]
    # include_inactive=False → должен быть фильтр fb_ads.is_active = true
    assert "fb_ads.is_active = true" in sql
    # LATERAL обязательно с фильтром по cycle_ts (partition pruning)
    assert "cycle_ts >= NOW() - make_interval" in sql


# Дополнительный sanity: _build_metrics_dict напрямую без _build_row_dict.
def test_build_metrics_dict_direct() -> None:
    """Прямой вызов _build_metrics_dict с заполненными полями."""
    row = _FakeRow(
        m_cycle_ts=datetime(2026, 3, 15, 9, 30, tzinfo=UTC),
        m_spend=Decimal("100.00"),
        m_leads=10,
        m_cost_per_lead=Decimal("10.00"),
        m_deposits=2,
    )
    m = _build_metrics_dict(row)
    assert m is not None
    assert m["cycle_ts"] == "2026-03-15T09:30:00+00:00"
    assert m["spend"] == "100.00"
    assert m["leads"] == 10
    assert m["cost_per_lead"] == "10.00"
    assert m["deposits"] == 2
