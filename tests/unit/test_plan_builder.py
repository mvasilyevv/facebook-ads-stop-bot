# -*- coding: utf-8 -*-
"""Тесты PlanBuilder: разворачивание CampaignSpec в список PlanAction."""

from core.campaign_creator.plan_builder import build_plan
from core.campaign_creator.plan_types import AdsetSpec, CampaignSpec


def _spec(n_adsets=2, n_creos=2):
    return CampaignSpec(
        offer_code="KE_CR2",
        cabinet_id="act",
        pixel_id="PX",
        landing_url="https://x",
        countries=["KE"],
        daily_budget=50.0,
        attribution_days=7,
        budget_level="CBO",
        adsets=[
            AdsetSpec(
                name_suffix=f"A{i}",
                creo_subfolder=str(i + 1),
                headline="H",
                primary_text="P",
                creatives=[f"v{j}.mp4" for j in range(n_creos)],
            )
            for i in range(n_adsets)
        ],
    )


# Сценарий: spec на 1 адсет × 1 креатив разворачивается в линейный план с create_campaign и save_draft
def test_build_plan_single_adset_single_ad():
    spec = _spec(1, 1)
    plan = build_plan(spec)
    names = [a.step for a in plan]
    assert names[0] == "create_campaign"
    assert names[-1] == "save_draft"
    assert "set_geo" in names
    assert "upload_creatives" in names
    assert "duplicate_adset" not in names
    assert "duplicate_ad" not in names


# Сценарий: spec на 2 адсета × 2 креатива использует duplicate_adset и duplicate_ad
def test_build_plan_two_adsets_two_creos_uses_duplicate():
    spec = _spec(2, 2)
    plan = build_plan(spec)
    names = [a.step for a in plan]
    assert names.count("duplicate_adset") == 1
    assert names.count("duplicate_ad") == 2
    assert names.count("reattach_creative") >= 1


# Сценарий: параметры PlanAction корректно прокидываются из spec
def test_build_plan_passes_params():
    spec = _spec(1, 1)
    plan = build_plan(spec)
    upload = next(a for a in plan if a.step == "upload_creatives")
    assert upload.params["subfolder"] == "1"
    assert upload.params["file"] == "v0.mp4"
    geo = next(a for a in plan if a.step == "set_geo")
    assert geo.params["countries"] == ["KE"]
