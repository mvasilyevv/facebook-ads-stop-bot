# -*- coding: utf-8 -*-
"""Unit-тесты pydantic-схем роутера campaigns_create (без БД).

Проверяют валидацию входа/выхода, переиспользование CampaignConfig и edge-кейсы
(бюджет hard-cap, пустые поля, дефолты). Money-safety: невалидный бюджет/конфиг
должен падать ещё на схеме, до создания run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.campaigns_create import (
    AdsetPlanOut,
    CampaignConfigIn,
    CampaignPlanOut,
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
    act_id/daily_budget/countries на верхнем уровне, campaigns[] с adset_count.
    """
    return {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "offer_code": "GH_CR",
        "byer_tag": "MV",
        "destination_link": "https://example.com",
        "start_date": "2026-07-01",
        "budget_level": "campaign",
        "daily_budget": "200.00",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "countries": ["DE"],
        "age_min": 18,
        "age_max": 65,
        "advantage_audience": True,
        "click_through_days": 1,
        "view_through_days": 1,
        "ad_text": {"mode": "text", "primary": "играй"},
        "campaigns": [{"key": "static", "adset_count": 2, "concept_refs": ["a.jpg", "b.jpg"]}],
        "copies_per_concept": None,
        "creo_root": "abc123",
        "url_tags": "sub2=MV",
    }


def _valid_config_dict() -> dict:
    """Минимально-валидный CampaignConfig для теста схем."""
    return {
        "account": {
            "act_id": "123",
            "page_id": "100",
            "pixel_id": "200",
            "timezone_name": "America/Los_Angeles",
            "currency": "USD",
            "account_context_observed_at": "2026-06-30T12:00:00+00:00",
        },
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "start_date": "2026-07-01",
        "budget": {"currency": "USD", "daily_amount": "3.00", "bid_amount": "5.00"},
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


_NOW = datetime(2026, 6, 30, 12, tzinfo=UTC)
_OBSERVED_AT = datetime(2026, 6, 30, 11, tzinfo=UTC)


def _to_domain(
    body: CampaignConfigIn | ValidateIn | LaunchIn,
    *,
    timezone_name: str = "America/Los_Angeles",
    currency: str = "USD",
    now: datetime = _NOW,
):
    converter = body.to_domain if isinstance(body, CampaignConfigIn) else body.domain_config
    return converter(
        timezone_name=timezone_name,
        currency=currency,
        account_context_observed_at=_OBSERVED_AT,
        now=now,
    )


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


# ValidateIn принимает только канонический плоский контракт.
def test_validate_in_wraps_config() -> None:
    body = ValidateIn(config=_flat_config_dict())
    assert body.config.offer_code == "GH_CR"
    assert _to_domain(body).targeting.geo_countries() == ["DE", "AQ"]


# LaunchIn пробрасывает невалидный бюджет в доменную money-валидацию.
def test_launch_in_propagates_budget_hard_cap() -> None:
    cfg = _flat_config_dict()
    cfg["daily_budget"] = "100000.01"
    with pytest.raises(ValueError):
        _to_domain(LaunchIn(config=cfg))


# Слишком короткий бюджет (опечатка) тоже отклоняется на схеме.
def test_launch_in_rejects_zero_budget() -> None:
    cfg = _flat_config_dict()
    cfg["daily_budget"] = "0"
    with pytest.raises(ValueError):
        _to_domain(LaunchIn(config=cfg))


# Идемпотентность вычисляет сервер из канонического конфига; клиент не может
# подменить ключ и случайно склеить разные money-запуски.
def test_launch_in_rejects_client_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        LaunchIn(config=_flat_config_dict(), idempotency_key="client-key")


# UploadConceptsOut агрегирует список концептов с размерами.
def test_upload_concepts_out_shape() -> None:
    out = UploadConceptsOut(
        upload_id="abc",
        upload_dir="/tmp/abc",
        concepts=[
            UploadedConceptOut(ref="a.jpg", original_name="a.jpg", size_bytes=10),
            UploadedConceptOut(ref="b.jpg", original_name="b.jpg", size_bytes=20),
        ],
        added_refs=["a.jpg", "b.jpg"],
        total_bytes=30,
    )
    assert len(out.concepts) == 2
    assert out.total_bytes == 30


# ValidatePlanOut несёт скалярные счётчики структуры.
def test_validate_plan_out_counts() -> None:
    plan = ValidatePlanOut(
        offer_code="GH_CR",
        creation_policy="all_paused",
        copies_per_concept=2,
        campaign_count=1,
        adset_count=2,
        ad_count=4,
        campaigns=[],
        start_date="2026-07-01",
        start_time="2026-07-01T00:00:00-07:00",
        timezone_name="America/Los_Angeles",
        currency="USD",
        account_context_observed_at=_OBSERVED_AT,
    )
    assert plan.ad_count == 4
    assert plan.creation_policy == "all_paused"


@pytest.mark.parametrize("model", [AdsetPlanOut, CampaignPlanOut])
def test_validate_plan_rejects_non_paused_status(model: type) -> None:
    payload = {"name": "target", "status": "ACTIVE"}
    if model is AdsetPlanOut:
        payload["ad_count"] = 1
    else:
        payload.update({"key": "campaign", "adsets": []})

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_campaign_config_rejects_legacy_launch_state() -> None:
    cfg = _flat_config_dict()
    cfg["launch_state"] = "campaign_paused"
    with pytest.raises(ValidationError):
        CampaignConfigIn.model_validate(cfg)


# ────────────── CRIT-2: контракт фронт↔бэк (плоская форма) ──────────────


# CampaignConfigIn.to_domain маппит плоский конфиг фронта в доменный вложенный.
def test_flat_config_to_domain_mapping() -> None:
    cfg_in = CampaignConfigIn.model_validate(_flat_config_dict())
    dom = _to_domain(cfg_in)
    # Account timezone/currency come only from the server-owned context.
    assert dom.account.act == "act_123"
    assert dom.account.timezone_name == "America/Los_Angeles"
    assert dom.account.currency == "USD"
    assert dom.account.pixel_id == "200"
    assert dom.budget.daily_amount == "200.00"
    assert dom.budget.daily_minor_units == 20000
    assert dom.budget.level == "campaign"
    # targeting: countries + авто-AQ по SOP.
    assert dom.targeting.geo_countries() == ["DE", "AQ"]
    assert dom.targeting.age_max == 65
    # attribution.
    assert dom.attribution.click_through_days == 1
    # ad_text: фронтовый mode 'text' → доменный 'full', primary → message.
    assert dom.ad_text.mode == "full"
    assert dom.ad_text.message == "играй"
    # campaigns: adset_count=2 → 2 доменных adset'а, тип медиа не в имени.
    assert len(dom.campaigns) == 1
    block = dom.campaigns[0]
    assert block.key == "static"
    assert len(block.adsets) == 2
    assert "adset.pro" in block.name
    assert "static" not in block.name and "video" not in block.name
    # ЕДИНЫЙ источник концептов: concept_refs проброшен в доменный блок (не теряется).
    assert block.concept_refs == ["a.jpg", "b.jpg"]
    # creo_root проброшен (upload_id).
    assert dom.creo_root == "abc123"
    assert dom.url_tags_template == "sub2=MV"


# ValidateIn принимает плоскую форму фронта; concept_refs остаются единственным
# источником раскладки.
def test_validate_in_accepts_flat_config() -> None:
    body = ValidateIn(config=_flat_config_dict())
    dom = _to_domain(body)
    assert dom.offer_code == "GH_CR"
    assert dom.budget.daily_minor_units == 20000
    assert dom.targeting.geo_countries() == ["DE", "AQ"]
    assert dom.campaigns[0].concept_refs == ["a.jpg", "b.jpg"]


# LaunchIn принимает плоскую форму фронта (тот же config-маппинг).
def test_launch_in_accepts_flat_config() -> None:
    body = LaunchIn(config=_flat_config_dict())
    dom = _to_domain(body)
    assert dom.offer_code == "GH_CR"
    assert dom.budget.daily_minor_units == 20000
    assert dom.account.act == "act_123"


def test_launch_in_canonicalizes_and_deduplicates_selected_offer_accounts() -> None:
    body = LaunchIn(
        config=_flat_config_dict(),
        ad_account_ids=["act_222", "111", "222"],
    )

    assert body.ad_account_ids == ["222", "111"]


# Второй источник количества концептов запрещён контрактом.
def test_launch_in_rejects_concept_counts_override() -> None:
    with pytest.raises(ValidationError):
        LaunchIn(config=_flat_config_dict(), concept_counts={"static": 5})


@pytest.mark.parametrize("field", ["creo_root", "concept_refs"])
def test_campaign_contract_requires_uploaded_concepts(field: str) -> None:
    config = _flat_config_dict()
    if field == "creo_root":
        config.pop("creo_root")
    else:
        config["campaigns"][0]["concept_refs"] = []
    with pytest.raises(ValidationError):
        LaunchIn(config=config)


# Money-инвариант: плоский конфиг с бюджетом выше hard-cap отклоняется при конвертации.
# (pydantic ValidationError — подкласс ValueError, эндпоинт ловит → 422.)
def test_flat_config_budget_hard_cap_rejected() -> None:
    cfg = _flat_config_dict()
    cfg["daily_budget"] = "100000.01"
    body = LaunchIn(config=cfg)
    with pytest.raises(ValueError):
        _to_domain(body)


# ────────────── exact currency-aware bid amount + SOP defaults ──────────────


# Major-unit bid_amount доходит до точных Meta minor units.
def test_flat_config_bid_amount_reaches_budget() -> None:
    cfg = _flat_config_dict()
    cfg["bid_strategy"] = "COST_CAP"
    cfg["bid_amount"] = "7.50"
    dom = _to_domain(CampaignConfigIn.model_validate(cfg))
    assert dom.budget.bid_strategy == "COST_CAP"
    assert dom.budget.bid_minor_units == 750


# COST_CAP без bid_amount отклоняется при конвертации.
def test_flat_config_cost_cap_without_bid_rejected() -> None:
    cfg = _flat_config_dict()
    cfg["bid_strategy"] = "COST_CAP"
    cfg["bid_amount"] = None
    with pytest.raises(ValueError):
        _to_domain(CampaignConfigIn.model_validate(cfg))


# SOP-дефолты плоской схемы: bid_strategy COST_CAP, age_min 21, bid_amount отсутствует.
def test_flat_config_sop_defaults() -> None:
    cfg = _flat_config_dict()
    del cfg["bid_strategy"]  # дефолт схемы
    del cfg["age_min"]  # дефолт схемы
    cfg_in = CampaignConfigIn.model_validate(cfg)
    assert cfg_in.bid_strategy == "COST_CAP"
    assert cfg_in.age_min == 21
    assert cfg_in.bid_amount is None


# Доменные SOP-дефолты Budget/Targeting: COST_CAP и age_min 21.
def test_domain_sop_defaults() -> None:
    from core.campaign_builder.config import Budget, Targeting

    assert Budget(currency="EUR", daily_amount="3.00", bid_amount="5.00").bid_strategy == "COST_CAP"
    assert Targeting(countries=["DE"]).age_min == 21
    # Дефолтный COST_CAP без ставки → ValueError (money-инвариант).
    with pytest.raises(ValueError):
        Budget(currency="EUR", daily_amount="3.00")


# Внутренняя вложенная модель не является вторым публичным API-контрактом.
def test_nested_config_is_rejected() -> None:
    nested = _valid_config_dict()
    with pytest.raises(ValidationError):
        ValidateIn(config=nested)
    with pytest.raises(ValidationError):
        LaunchIn(config=nested)


def test_campaign_contract_rejects_unknown_fields() -> None:
    config = _flat_config_dict()
    config["account"] = {"act_id": "shadow-account"}
    with pytest.raises(ValidationError):
        LaunchIn(config=config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tz_offset", -7),
        ("timezone_name", "America/Los_Angeles"),
        ("currency", "USD"),
        ("currency_exponent", 2),
    ],
)
def test_client_cannot_forge_account_context(field: str, value: object) -> None:
    config = _flat_config_dict()
    config[field] = value
    with pytest.raises(ValidationError):
        LaunchIn(config=config)


@pytest.mark.parametrize("field", ["daily_budget", "bid_amount"])
def test_money_inputs_must_be_decimal_strings(field: str) -> None:
    config = _flat_config_dict()
    config["bid_strategy"] = "COST_CAP"
    config["bid_amount"] = "5.00"
    config[field] = 5
    with pytest.raises(ValidationError):
        CampaignConfigIn.model_validate(config)


def test_default_start_date_uses_next_cabinet_local_day_not_utc_day() -> None:
    config = _flat_config_dict()
    config["start_date"] = None
    # UTC is still July 1 while Pacific/Kiritimati is already July 2.
    now = datetime(2026, 7, 1, 12, 30, tzinfo=UTC)
    domain = _to_domain(
        CampaignConfigIn.model_validate(config),
        timezone_name="Pacific/Kiritimati",
        now=now,
    )
    assert domain.start_date == "2026-07-03"
    assert domain.start_time == "2026-07-03T00:00:00+14:00"


@pytest.mark.parametrize(
    ("currency", "daily", "bid", "daily_minor", "bid_minor"),
    [
        ("JPY", "1200", "75", 1200, 75),
        ("KWD", "12.345", "0.125", 12345, 125),
    ],
)
def test_currency_exponent_contract_is_exact(
    currency: str,
    daily: str,
    bid: str,
    daily_minor: int,
    bid_minor: int,
) -> None:
    config = _flat_config_dict()
    config.update(
        {
            "daily_budget": daily,
            "bid_strategy": "COST_CAP",
            "bid_amount": bid,
        }
    )
    domain = _to_domain(CampaignConfigIn.model_validate(config), currency=currency)
    assert domain.budget.daily_minor_units == daily_minor
    assert domain.budget.bid_minor_units == bid_minor
    assert domain.account.currency == currency


def test_currency_precision_loss_is_rejected() -> None:
    config = _flat_config_dict()
    config["daily_budget"] = "1.01"
    with pytest.raises(ValueError, match="decimal places"):
        _to_domain(CampaignConfigIn.model_validate(config), currency="JPY")


# Метка кампании добавляется в конец имени кампании и каждого adset'а.
def test_flat_config_label_appended() -> None:
    cfg = _flat_config_dict()
    cfg["campaigns"][0]["label"] = "TEST-A"
    dom = _to_domain(CampaignConfigIn.model_validate(cfg))
    block = dom.campaigns[0]
    assert block.name.endswith("| TEST-A")
    assert all(a.name.endswith("| TEST-A") for a in block.adsets)


# Без метки — имя без сегмента типа и без хвоста.
def test_flat_config_no_label_no_type_segment() -> None:
    dom = _to_domain(CampaignConfigIn.model_validate(_flat_config_dict()))
    block = dom.campaigns[0]
    assert "adset.pro" in block.name
    assert "static" not in block.name and "video" not in block.name
