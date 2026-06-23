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
    CampaignConfigIn,
    LaunchIn,
    PresetIn,
    UploadConceptsOut,
    UploadedConceptOut,
    ValidateIn,
    ValidatePlanOut,
)


def _flat_config_dict() -> dict:
    """Плоский конфиг — РОВНО та форма, что шлёт фронт (web buildConfig / mini).

    Источник истины — frontend/src/stores/campaignWizard.ts::buildConfig:
    act_id/daily_budget_cents/countries на верхнем уровне, campaigns[] с adset_count.
    """
    return {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "tz_offset": -7,
        "offer_code": "GH_CR",
        "byer_tag": "MV",
        "destination_link": "https://example.com",
        "start_date": "2026-07-01",
        "budget_level": "campaign",
        "daily_budget_cents": 20000,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "countries": ["DE"],
        "age_min": 18,
        "age_max": 65,
        "advantage_audience": True,
        "click_through_days": 1,
        "view_through_days": 1,
        "ad_text": {"mode": "text", "primary": "играй"},
        "campaigns": [
            {"key": "static", "kind": "image", "adset_count": 2, "concept_refs": ["a.jpg", "b.jpg"]}
        ],
        "copies_per_concept": None,
        "creo_root": "abc123",
        "launch_state": "campaign_paused",
        "url_tags": "sub2=MV",
    }


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


# ────────────── CRIT-2: контракт фронт↔бэк (плоская форма) ──────────────


# CampaignConfigIn.to_domain маппит плоский конфиг фронта в доменный вложенный.
def test_flat_config_to_domain_mapping() -> None:
    cfg_in = CampaignConfigIn.model_validate(_flat_config_dict())
    dom = cfg_in.to_domain()
    # account: tz_offset int(-7) → ISO '-07:00', act_id с префиксом.
    assert dom.account.act == "act_123"
    assert dom.account.tz_offset == "-07:00"
    assert dom.account.pixel_id == "200"
    # budget: daily_budget_cents → budget.daily_cents.
    assert dom.budget.daily_cents == 20000
    assert dom.budget.level == "campaign"
    # targeting: countries + авто-AQ по SOP.
    assert dom.targeting.geo_countries() == ["DE", "AQ"]
    assert dom.targeting.age_max == 65
    # attribution.
    assert dom.attribution.click_through_days == 1
    # ad_text: фронтовый mode 'text' → доменный 'full', primary → message.
    assert dom.ad_text.mode == "full"
    assert dom.ad_text.message == "играй"
    # campaigns: adset_count=2 → 2 доменных adset'а, kind image → label static.
    assert len(dom.campaigns) == 1
    block = dom.campaigns[0]
    assert block.key == "static"
    assert block.kind == "image"
    assert len(block.adsets) == 2
    assert "static" in block.name
    # creo_root проброшен (upload_id).
    assert dom.creo_root == "abc123"


# concept_counts извлекается из длины concept_refs каждого блока.
def test_flat_config_concept_counts() -> None:
    cfg_in = CampaignConfigIn.model_validate(_flat_config_dict())
    assert cfg_in.concept_counts() == {"static": 2}


# ValidateIn принимает плоскую форму фронта и нормализует config + concept_counts.
def test_validate_in_accepts_flat_config() -> None:
    body = ValidateIn(config=_flat_config_dict())
    dom = body.domain_config()
    assert dom.offer_code == "GH_CR"
    assert dom.budget.daily_cents == 20000
    assert dom.targeting.geo_countries() == ["DE", "AQ"]
    # concept_counts извлечён из плоского входа для build_campaign_spec.
    assert body.concept_counts_map() == {"static": 2}


# LaunchIn принимает плоскую форму фронта (тот же config-маппинг).
def test_launch_in_accepts_flat_config() -> None:
    body = LaunchIn(config=_flat_config_dict())
    dom = body.domain_config()
    assert dom.offer_code == "GH_CR"
    assert dom.budget.daily_cents == 20000
    assert dom.account.act == "act_123"


# launch тоже знает раскладку K: concept_counts из длины concept_refs (симметрия с validate).
def test_launch_in_concept_counts_map_from_flat() -> None:
    body = LaunchIn(config=_flat_config_dict())
    assert body.concept_counts_map() == {"static": 2}


# Явный concept_counts в теле launch перекрывает выведенный из concept_refs.
def test_launch_in_concept_counts_explicit_override() -> None:
    body = LaunchIn(config=_flat_config_dict(), concept_counts={"static": 5})
    assert body.concept_counts_map() == {"static": 5}


# Money-инвариант: плоский конфиг с бюджетом выше hard-cap отклоняется при конвертации.
# (pydantic ValidationError — подкласс ValueError, эндпоинт ловит → 422.)
def test_flat_config_budget_hard_cap_rejected() -> None:
    cfg = _flat_config_dict()
    cfg["daily_budget_cents"] = 100_000_00 + 1
    body = LaunchIn(config=cfg)
    with pytest.raises(ValueError):
        body.domain_config()


# Плоский конфиг с недопустимым kind → ValueError при конвертации (домен досверяет).
def test_flat_config_bad_kind_rejected() -> None:
    cfg = _flat_config_dict()
    cfg["campaigns"][0]["kind"] = "carousel"
    body = ValidateIn(config=cfg)
    with pytest.raises(ValueError):
        body.domain_config()


# Вложенная (legacy) форма по-прежнему принимается обоими телами (обратная совместимость).
def test_nested_config_still_accepted() -> None:
    nested = _valid_config_dict()
    assert ValidateIn(config=nested).domain_config().offer_code == "GH_CR"
    assert LaunchIn(config=nested).domain_config().offer_code == "GH_CR"
    # У вложенной формы concept_counts недоступен из тела → None (фолбэк раскладки).
    assert ValidateIn(config=nested).concept_counts_map() is None
    assert LaunchIn(config=nested).concept_counts_map() is None
