# -*- coding: utf-8 -*-
"""Unit-тесты pydantic-схем роутера campaigns_create (без БД).

Проверяют валидацию входа/выхода, переиспользование CampaignConfig и edge-кейсы
(бюджет hard-cap, пустые поля, дефолты). Money-safety: невалидный бюджет/конфиг
должен падать ещё на схеме, до создания run.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.campaigns_create import (
    LaunchIn,
    PresetIn,
    UploadConceptsOut,
    UploadedConceptOut,
    ValidateIn,
    ValidatePlanOut,
)


def _valid_config_dict() -> dict:
    """Минимально-валидный CampaignConfig для теста схем."""
    return {
        "account": {"act_id": "123", "page_id": "100", "pixel_id": "200"},
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "targeting": {"countries": ["DE"]},
        "campaigns": [
            {
                "key": "static",
                "name": "{byer} | {offer} | static | adset.pro | {date}",
                "kind": "image",
                "adsets": [
                    {"name": "as1", "dir": "as1", "glob": "*.jpg"},
                    {"name": "as2", "dir": "as2", "glob": "*.jpg"},
                ],
            }
        ],
    }


# PresetIn принимает SOP-дефолты и не требует опциональных полей.
def test_preset_in_defaults() -> None:
    p = PresetIn(name="GH base", act_id="act_1", page_id="100", pixel_id="200")
    assert p.objective == "OUTCOME_SALES"
    assert p.optimization_goal == "OFFSITE_CONVERSIONS"
    assert p.custom_event_type == "PURCHASE"
    assert p.special_ad_categories == ["NONE"]
    assert p.cta == "PLAY_GAME"
    assert p.click_through_days == 1


# Пустое имя пресета отклоняется на валидации.
def test_preset_in_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        PresetIn(name="", act_id="act_1", page_id="100", pixel_id="200")


# ValidateIn оборачивает валидный CampaignConfig.
def test_validate_in_wraps_config() -> None:
    body = ValidateIn(config=_valid_config_dict())
    assert body.config.offer_code == "GH_CR"
    assert body.config.targeting.geo_countries() == ["DE", "AQ"]


# LaunchIn пробрасывает невалидный бюджет вглубь CampaignConfig → ValidationError.
def test_launch_in_propagates_budget_hard_cap() -> None:
    cfg = _valid_config_dict()
    cfg["budget"] = {"daily_cents": 100_000_00 + 1}  # выше hard-cap
    with pytest.raises(ValidationError):
        LaunchIn(config=cfg)


# Слишком короткий бюджет (опечатка) тоже отклоняется на схеме.
def test_launch_in_rejects_tiny_budget() -> None:
    cfg = _valid_config_dict()
    cfg["budget"] = {"daily_cents": 1}  # ниже MIN
    with pytest.raises(ValidationError):
        LaunchIn(config=cfg)


# idempotency_key опционален и ограничен по длине.
def test_launch_in_idempotency_key_optional() -> None:
    body = LaunchIn(config=_valid_config_dict())
    assert body.idempotency_key is None
    with pytest.raises(ValidationError):
        LaunchIn(config=_valid_config_dict(), idempotency_key="x" * 129)


# UploadConceptsOut агрегирует список концептов с размерами.
def test_upload_concepts_out_shape() -> None:
    out = UploadConceptsOut(
        upload_id="abc",
        upload_dir="/tmp/abc",
        concepts=[
            UploadedConceptOut(ref="a.jpg", original_name="a.jpg", size_bytes=10),
            UploadedConceptOut(ref="b.jpg", original_name="b.jpg", size_bytes=20),
        ],
        total_bytes=30,
    )
    assert len(out.concepts) == 2
    assert out.total_bytes == 30


# ValidatePlanOut несёт скалярные счётчики структуры.
def test_validate_plan_out_counts() -> None:
    plan = ValidatePlanOut(
        offer_code="GH_CR",
        launch_state="campaign_paused",
        copies_per_concept=2,
        campaign_count=1,
        adset_count=2,
        ad_count=4,
        campaigns=[],
    )
    assert plan.ad_count == 4
    assert plan.launch_state == "campaign_paused"
