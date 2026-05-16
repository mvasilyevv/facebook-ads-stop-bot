# -*- coding: utf-8 -*-
"""Тесты реестра шагов campaign_creator."""

from __future__ import annotations

import pytest

from core.campaign_creator.steps.base import BaseStep
from core.campaign_creator.steps.registry import (
    STEPS_ORDER,
    build_pipeline,
    build_step,
    step_idempotent,
)


# Сценарий: STEP_REGISTRY содержит все основные шаги, доступные по имени.
def test_step_registry_contains_core_steps():
    from core.campaign_creator.steps.registry import STEP_REGISTRY

    expected = {
        "create_campaign",
        "create_adset",
        "set_budget",
        "set_attribution",
        "set_pixel_event",
        "set_geo",
        "set_age",
        "set_schedule_start",
        "set_conversion_location",
        "click_next",
        "click_next_to_ad",
        "upload_creatives",
        "fill_texts",
        "set_cta",
        "set_tracking_url",
        "save_draft",
    }
    assert expected.issubset(set(STEP_REGISTRY.keys()))


# Сценарий: каждое значение реестра — подкласс BaseStep, у которого .name совпадает с ключом.
def test_step_registry_consistent():
    from core.campaign_creator.steps.registry import STEP_REGISTRY

    for name, cls in STEP_REGISTRY.items():
        assert issubclass(cls, BaseStep)
        assert cls.name == name


# Сценарий: STEPS_ORDER (legacy) тоже остаётся доступен пока execute_steps его использует.
def test_steps_order_legacy_keeps_save_draft_last():
    assert STEPS_ORDER[0] == "create_campaign"
    assert STEPS_ORDER[-1] == "save_draft"


# Сценарий: build_step создаёт инстанс с правильным name.
def test_build_step_returns_instance():
    step = build_step("set_budget")
    assert isinstance(step, BaseStep)
    assert step.name == "set_budget"


# Сценарий: build_step падает с KeyError на неизвестном имени.
def test_build_step_unknown_name():
    with pytest.raises(KeyError):
        build_step("never_existed")


# Сценарий: build_pipeline без аргумента отдаёт полный пайплайн.
def test_build_pipeline_full():
    pipeline = build_pipeline()
    assert [s.name for s in pipeline] == STEPS_ORDER


# Сценарий: build_pipeline(start_from=X) обрезает префикс.
def test_build_pipeline_from_step():
    pipeline = build_pipeline(start_from="set_geo")
    names = [s.name for s in pipeline]
    assert names[0] == "set_geo"
    assert names[-1] == "save_draft"
    assert "set_budget" not in names


# Сценарий: флаг idempotent доступен для нужных шагов.
def test_idempotent_flag():
    assert step_idempotent("set_attribution") is True
    assert step_idempotent("set_cta") is True
    assert step_idempotent("create_campaign") is False
    assert step_idempotent("upload_creatives") is False
