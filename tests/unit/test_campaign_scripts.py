# -*- coding: utf-8 -*-
"""Тесты планирования сценария создания кампаний."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

import pytest

from apps.api.routers.campaign_scripts import router
from apps.api.schemas import CampaignScriptPlanSchema
from core.campaign_scripts import (
    CampaignCreativeValidationError,
    CampaignScriptConfig,
    build_campaign_script_plan,
    inspect_creative_folder,
    list_creative_folders,
)


def _write_creative_tree(base):
    """Создаёт валидную тестовую структуру папок креативов."""
    folder = base / "DRC_CR2_2026-04-26_22-03-32_3creo_3copies"
    for copy_index in [1, 2, 3]:
        copy_dir = folder / str(copy_index)
        copy_dir.mkdir(parents=True)
        for creative_id in ["CR011", "CR012", "CR013"]:
            (copy_dir / f"DRC_CR2_{creative_id}_{copy_index}.jpeg").write_bytes(b"media")
    return folder


# Сценарий: план строится из папки копий и сохраняет URL-параметры с макросами Meta.
@pytest.mark.asyncio
async def test_build_campaign_script_plan_from_creative_folder(tmp_path):
    folder = _write_creative_tree(tmp_path)
    inspected = await inspect_creative_folder(folder.name, root=tmp_path)

    plan = build_campaign_script_plan(
        folder=inspected,
        config=CampaignScriptConfig(
            offer_code="DRC_CR2",
            offer_country_name="Демократическая Республика Конго",
            cabinet_id="1472252497899089",
            generation_date=date(2026, 5, 27),
        ),
    )

    assert plan.adset_count == 3
    assert plan.ad_count == 9
    assert plan.campaign_name == "MV | DRC | CR2 | adset.pro | 28.05"
    assert plan.sub2 == "MV"
    assert plan.location_plan.add_locations == [
        "Антарктика",
        "Демократическая Республика Конго",
    ]
    assert plan.location_plan.required_location_type == "Страна/регион"
    assert "город" in plan.location_plan.rejected_location_terms
    assert "ручного создания" in " ".join(plan.safety_notes)
    assert plan.adsets[1].name == "2"
    assert plan.adsets[1].ads[0].media_search_name == "DRC_CR2_CR011_2"
    assert plan.adsets[1].ads[0].url_params == (
        "sub2=MV&sub3=DRC_CR2_CR011&sub4=1472252497899089"
        "&sub5={{campaign.name}}&sub6={{adset.name}}&sub7={{ad.name}}"
    )
    guide = CampaignScriptPlanSchema(**asdict(plan)).manual_guide
    assert [section.title for section in guide] == [
        "Кампания",
        "Группа объявлений",
        "Объявления",
        "Копии групп",
    ]
    ad_items = guide[2].items
    campaign_items = guide[0].items
    adset_items = guide[1].items
    assert any(
        item.label == "Название" and item.value == "MV | DRC | CR2 | adset.pro | 28.05"
        for item in campaign_items
    )
    assert not any(item.label == "Цель" for item in campaign_items)
    assert not any("Бюджет" in item.label for item in campaign_items)
    assert not any(
        item.label
        in {
            "Имя",
            "Место конверсии",
            "Событие",
            "Pixel",
            "Переход по клику",
            "Дата",
            "Время",
            "Удалить исходное гео",
        }
        for item in adset_items
    )
    assert any(
        item.label == "Ad 1: поиск медиа" and item.value == "DRC_CR2_CR011_1" for item in ad_items
    )
    assert not any(
        item.label
        in {
            "Ad 1: источник креативов",
            "Ad 1: формат",
            "Ad 1: файл",
            "Ad 1: URL",
            "Ad 1: CTA",
            "Ad 1: основной текст",
            "Ad 1: заголовок",
            "Ad 1: описание",
        }
        for item in ad_items
    )
    copied_items = guide[3].items
    assert any(
        item.label == "Группа 2, ad 1: поиск медиа" and item.value == "DRC_CR2_CR011_2"
        for item in copied_items
    )
    assert not any(item.label == "Группа 2, ad 1: файл" for item in copied_items)
    assert not any(item.label == "Группа 2, ad 1: URL params" for item in copied_items)


# Сценарий: старые executor-ручки больше не публикуются в campaign-create router.
def test_campaign_create_router_exposes_only_safe_plan_routes():
    paths = {route.path for route in router.routes}

    assert "/api/tools/campaign-create/folders" in paths
    assert "/api/tools/campaign-create/plan" in paths
    assert not any("execute" in path or "stages" in path or "steps" in path for path in paths)


# Сценарий: список папок показывает все директории и помечает невалидные причиной.
@pytest.mark.asyncio
async def test_list_creative_folders_returns_all_folder_summaries(tmp_path):
    valid = _write_creative_tree(tmp_path)
    invalid = tmp_path / "broken"
    (invalid / "2").mkdir(parents=True)
    (invalid / "2" / "DRC_CR2_CR011_2.jpeg").write_bytes(b"media")

    folders = await list_creative_folders(root=tmp_path)
    by_name = {folder.name: folder for folder in folders}

    assert set(by_name) == {valid.name, invalid.name}
    assert by_name[valid.name].is_valid is True
    assert by_name[valid.name].adset_count == 3
    assert by_name[valid.name].creative_count == 3
    assert by_name[valid.name].media_type == "image"
    assert by_name[invalid.name].is_valid is False
    assert "должны идти подряд" in by_name[invalid.name].validation_error


# Сценарий: файл без суффикса номера копии отклоняется до построения плана.
@pytest.mark.asyncio
async def test_inspect_creative_folder_rejects_file_without_copy_suffix(tmp_path):
    folder = tmp_path / "bad_suffix"
    copy_dir = folder / "1"
    copy_dir.mkdir(parents=True)
    (copy_dir / "DRC_CR2_CR011.jpeg").write_bytes(b"media")

    with pytest.raises(CampaignCreativeValidationError, match="должен заканчиваться"):
        await inspect_creative_folder(folder.name, root=tmp_path)
