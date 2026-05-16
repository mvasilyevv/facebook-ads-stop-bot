# -*- coding: utf-8 -*-
"""Unit-тесты StepContext / AdsetSpec."""

from __future__ import annotations

from core.campaign_creator.steps.base import AdsetSpec, StepContext


def test_adsetspec_holds_required_fields():
    """AdsetSpec — суффикс к имени и опциональные тексты, имя и подпапка от индекса."""
    spec = AdsetSpec(name_suffix="ADS1", headline="hh", primary_text="pp")
    assert spec.name_suffix == "ADS1"
    assert spec.headline == "hh"
    assert spec.primary_text == "pp"
    assert spec.display_name(0) == "1 | ADS1"
    assert spec.subfolder(0) == "1"


def test_adsetspec_defaults_and_naming_without_suffix():
    """Без суффикса имя адсета — это просто его номер."""
    spec = AdsetSpec()
    assert spec.name_suffix == ""
    assert spec.headline == ""
    assert spec.primary_text == ""
    assert spec.display_name(2) == "3"
    assert spec.subfolder(2) == "3"


def test_stepcontext_minimal_construction():
    """StepContext должен принимать полный набор параметров автосоздания."""
    ctx = StepContext(
        offer_code="KE_CR2",
        cabinet_id="act_1",
        campaign_name="CR2 | KE_CR2 | MV | 12.05",
        pixel_id="123",
        landing_url="https://x.com",
        geo_code="KE",
        geo_slot_name="Кения",
        daily_budget=20.0,
        attribution_days=7,
        budget_level="CBO",
        iter_num=2,
        adsets=[],
        creo_folder="/tmp/creo",
    )
    assert ctx.budget_level == "CBO"
    assert ctx.attribution_days == 7
    assert ctx.adsets == []
    assert ctx.extra == {}
