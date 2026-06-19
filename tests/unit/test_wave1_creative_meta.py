# -*- coding: utf-8 -*-
"""Волна 1: контракт новых полей (превью крео + метаданные адсета).

Проверяем сквозной shape БЕЗ БД: dataclass ScannedAdRow → snapshot row_dict →
Pydantic AdSnapshotOut. Гарантия, что 7 новых полей не теряются ни на одной границе.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from apps.api.routers.v1.schemas.dashboard import AdSnapshotOut
from core.scanner.models import ScannedAdRow


# ScannedAdRow: новые поля имеют дефолт "" (не участвуют в обязательном контракте).
def test_scanned_ad_row_wave1_defaults_empty() -> None:
    """Минимальный ScannedAdRow без новых полей → они = "" (back-compat скана)."""
    row = ScannedAdRow(
        fb_ad_id="1",
        campaign_name="C",
        adset_name="A",
        ad_name="AD",
        delivery_status="ACTIVE",
        spend=Decimal("0"),
    )
    assert row.creative_thumb_url == ""
    assert row.creative_image_url == ""
    assert row.adset_pixel_id == ""
    assert row.adset_daily_budget == ""
    assert row.adset_lifetime_budget == ""
    assert row.adset_budget_remaining == ""
    assert row.adset_learning_stage == ""


# ScannedAdRow: новые поля заполняются и доступны (для writers/pipeline).
def test_scanned_ad_row_wave1_populated() -> None:
    """Заполненные поля доходят до атрибутов dataclass (frozen)."""
    row = ScannedAdRow(
        fb_ad_id="1",
        campaign_name="C",
        adset_name="A",
        ad_name="AD",
        delivery_status="ACTIVE",
        spend=Decimal("0"),
        creative_thumb_url="https://cdn/t.jpg",
        adset_pixel_id="999",
        adset_learning_stage="LEARNING_LIMITED",
    )
    assert row.creative_thumb_url == "https://cdn/t.jpg"
    assert row.adset_pixel_id == "999"
    assert row.adset_learning_stage == "LEARNING_LIMITED"


# AdSnapshotOut: новые поля опциональны (None по умолчанию) и принимаются.
def test_ad_snapshot_out_wave1_optional() -> None:
    """Схема валидна без новых полей (None) и с ними."""
    minimal = AdSnapshotOut(
        fb_ad_id="1",
        internal_id=str(uuid.uuid4()),
        ad_name="AD",
        is_active=True,
    )
    assert minimal.creative_thumb_url is None
    assert minimal.learning_stage is None

    full = AdSnapshotOut(
        fb_ad_id="1",
        internal_id=str(uuid.uuid4()),
        ad_name="AD",
        is_active=True,
        creative_thumb_url="https://cdn/t.jpg",
        creative_image_url="https://cdn/f.jpg",
        adset_pixel_id="123",
        adset_daily_budget="500",
        adset_lifetime_budget="9000",
        adset_budget_remaining="350",
        learning_stage="LEARNING",
    )
    assert full.creative_thumb_url == "https://cdn/t.jpg"
    assert full.adset_daily_budget == "500"
    assert full.learning_stage == "LEARNING"


# Сквозной shape: row_dict из snapshot валидируется как AdSnapshotOut без потерь.
def test_snapshot_dict_validates_as_schema() -> None:
    """_build_row_dict с новыми полями → AdSnapshotOut принимает их 1:1."""
    from core.dashboard.snapshot import _build_row_dict
    from tests.unit.test_dashboard_snapshot import _FakeRow

    row = _FakeRow(
        creative_thumb_url="https://cdn/t.jpg",
        adset_pixel_id="123",
        adset_daily_budget="500",
        learning_stage="LEARNING",
    )
    out = AdSnapshotOut(**_build_row_dict(row))
    assert out.creative_thumb_url == "https://cdn/t.jpg"
    assert out.adset_pixel_id == "123"
    assert out.adset_daily_budget == "500"
    assert out.learning_stage == "LEARNING"
