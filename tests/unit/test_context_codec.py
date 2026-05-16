# -*- coding: utf-8 -*-
"""Тесты сериализации StepContext в dict и обратно."""

from __future__ import annotations

from core.campaign_creator.context_codec import context_from_dict, context_to_dict
from core.campaign_creator.steps.base import AdsetSpec, StepContext


def _make_context() -> StepContext:
    return StepContext(
        offer_code="DRC_CR2",
        cabinet_id="act_1",
        campaign_name="CR2 | DRC | MV | 25.03",
        pixel_id="pix1",
        landing_url="https://example.com",
        geo_code="UA",
        geo_slot_name="Украина",
        daily_budget=15.0,
        attribution_days=7,
        budget_level="CBO",
        iter_num=2,
        adsets=[
            AdsetSpec(name_suffix="A1", headline="H1", primary_text="P1"),
            AdsetSpec(name_suffix="", headline="", primary_text=""),
        ],
        creo_folder="/tmp/creo",
        extra={"offer_country_name": "Украина"},
    )


# Сценарий: round-trip сохраняет все поля и восстанавливает AdsetSpec.
def test_context_roundtrip():
    ctx = _make_context()
    data = context_to_dict(ctx)
    assert isinstance(data, dict)
    assert data["adsets"][0]["name_suffix"] == "A1"

    restored = context_from_dict(data)
    assert restored == ctx
    assert isinstance(restored.adsets[0], AdsetSpec)
    assert restored.adsets[1].name_suffix == ""


# Сценарий: dict, прочитанный из JSON, корректно превращается обратно.
def test_context_from_json_dict():
    import json

    ctx = _make_context()
    payload = json.loads(json.dumps(context_to_dict(ctx)))
    restored = context_from_dict(payload)
    assert restored.offer_code == ctx.offer_code
    assert restored.adsets[0].headline == "H1"
