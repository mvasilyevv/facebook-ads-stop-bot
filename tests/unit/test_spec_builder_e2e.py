# -*- coding: utf-8 -*-
"""End-to-end smoke: scan папки → spec_builder → build_plan."""

from __future__ import annotations

from core.campaign_creator.plan_builder import build_plan
from core.campaign_creator.spec_builder import build_campaign_spec_from_folder


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _make_creo(tmp_path):
    # 2 адсета × 2 креатива
    _touch(tmp_path / "1" / "CR004_1.jpeg")
    _touch(tmp_path / "1" / "CR004_2.jpeg")
    _touch(tmp_path / "2" / "CR005_1.jpeg")
    _touch(tmp_path / "2" / "CR005_2.jpeg")


# Сценарий: сборка spec из реальной папки и разворачивание в полный план.
def test_end_to_end_spec_to_plan(tmp_path):
    _make_creo(tmp_path)
    spec = build_campaign_spec_from_folder(
        creo_folder=tmp_path,
        offer_code="KE_CR2",
        cabinet_id="act_1",
        pixel_id="px",
        landing_url="https://example.com",
        countries=["KE"],
        daily_budget=20.0,
        attribution_days=7,
        budget_level="CBO",
        primary_text="PT",
        headline="HL",
        description="DESC",
    )
    assert len(spec.adsets) == 2
    assert spec.adsets[0].creo_subfolder == "1"
    assert spec.adsets[1].creo_subfolder == "2"
    assert spec.adsets[0].description == "DESC"

    plan = build_plan(spec)
    names = [a.step for a in plan]

    # Скелет уровня кампании.
    assert names[0] == "create_campaign"
    assert "set_budget" in names
    assert names[-1] == "save_draft"

    # Адсеты: rename_adset, set_*, click_next_to_ad, ad-блок, duplicate_adset, switch.
    assert names.count("rename_adset") == 2
    assert names.count("duplicate_adset") == 1
    assert names.count("switch_to_adset") == 1
    assert "click_next_to_ad" in names

    # Объявления: для первого адсета один upload + один duplicate_ad,
    # для дубль-адсета — два reattach + один duplicate_ad.
    assert names.count("upload_creatives") == 1
    assert names.count("duplicate_ad") == 2  # по одному в каждом адсете на 2-й креатив
    assert names.count("reattach_creative") >= 2

    # fill_texts и set_cta — на каждый адсет.
    assert names.count("fill_texts") == 2
    assert names.count("set_cta") == 2

    # Проверка description прокинута в fill_texts.
    fill = next(a for a in plan if a.step == "fill_texts")
    assert fill.params["description"] == "DESC"
    assert fill.params["headline"] == "HL"
    assert fill.params["primary_text"] == "PT"


# Сценарий: последовательность скелета корректна — сначала кампания,
# потом адсет 0 (rename → set_* → click_next_to_ad → ads), потом duplicate_adset.
def test_plan_order_invariants(tmp_path):
    _make_creo(tmp_path)
    spec = build_campaign_spec_from_folder(
        creo_folder=tmp_path,
        offer_code="X",
        cabinet_id="act",
        pixel_id="p",
        landing_url="https://x",
        countries=["KE"],
        daily_budget=10.0,
        attribution_days=7,
        budget_level="CBO",
    )
    names = [a.step for a in build_plan(spec)]

    i_create = names.index("create_campaign")
    i_click = names.index("click_next")
    i_rename0 = names.index("rename_adset")
    i_next_ad = names.index("click_next_to_ad")
    i_upload = names.index("upload_creatives")
    i_dup_adset = names.index("duplicate_adset")
    i_switch = names.index("switch_to_adset")
    i_save = names.index("save_draft")

    assert i_create < i_click < i_rename0 < i_next_ad < i_upload < i_dup_adset
    assert i_dup_adset < i_switch < i_save
