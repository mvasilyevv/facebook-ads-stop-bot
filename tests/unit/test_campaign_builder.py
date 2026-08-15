# -*- coding: utf-8 -*-
"""Unit-тесты движка core/campaign_builder (без БД, без сети).

Покрывают: валидацию CampaignConfig (budget hard-cap, +AQ авто, дефолт даты),
рендер имён/кодов креативов и canonical all-paused build_campaign_spec.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.campaign_builder import (
    EXEC_STEP_ORDER,
    Account,
    Budget,
    CampaignConfig,
    CampaignSpec,
    Targeting,
    build_campaign_spec,
    plan_execution_steps,
)
from core.campaign_builder.config import (
    MAX_DAILY_BUDGET,
    MAX_LIFETIME_BUDGET,
    AdsetConfig,
    CampaignBlock,
)
from core.campaign_builder.naming import creative_codes, render_name

# ---------------------- фикстуры конфигов ----------------------


def _account() -> Account:
    """Минимальный валидный кабинет."""
    return Account(
        act_id="123456789",
        page_id="111",
        pixel_id="222",
        timezone_name="America/New_York",
        currency="USD",
        account_context_observed_at="2026-06-17T12:00:00+00:00",
    )


def _image_block(concept_refs: list[str] | None = None) -> CampaignBlock:
    """Кампания со статикой и двумя adset'ами."""
    return CampaignBlock(
        key="static",
        name="{byer} | {offer} | static | adset.pro | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | static | s1 | {date}", dir="c1/a1", glob="*.jpg"),
            AdsetConfig(name="{byer} | {offer} | static | s2 | {date}", dir="c1/a2", glob="*.jpg"),
        ],
        concept_refs=["a.jpg"] if concept_refs is None else concept_refs,
    )


def _config(**overrides) -> CampaignConfig:
    """Собирает валидный CampaignConfig с дефолтами и переопределениями."""
    base = dict(
        account=_account(),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
        creo_root="~/Documents/FB_Agent_Creo/CR_GH",
        budget=Budget(currency="USD", daily_amount="3.00", bid_amount="5.00"),
        targeting=Targeting(countries=["GH"]),
        campaigns=[_image_block()],
    )
    base.update(overrides)
    return CampaignConfig(**base)


# ---------------------- immutable account evidence ----------------------


def test_account_canonicalizes_id_and_persists_explicit_currency_exponent():
    account = Account(
        act_id=" act_123456789 ",
        page_id="111",
        pixel_id="222",
        timezone_name="Asia/Tokyo",
        currency="JPY",
        account_context_observed_at="2026-06-17T12:00:00+00:00",
    )

    assert account.act_id == "123456789"
    assert account.currency_exponent == 0
    assert account.model_dump(mode="json")["currency_exponent"] == 0


def test_account_rejects_mismatched_currency_exponent():
    with pytest.raises(ValueError, match="currency_exponent"):
        Account(
            act_id="123",
            page_id="111",
            pixel_id="222",
            timezone_name="Asia/Tokyo",
            currency="JPY",
            currency_exponent=2,
            account_context_observed_at="2026-06-17T12:00:00+00:00",
        )


@pytest.mark.parametrize(
    ("model", "legacy_field"),
    [
        (
            lambda **extra: Account(
                act_id="123",
                page_id="111",
                pixel_id="222",
                timezone_name="UTC",
                currency="USD",
                account_context_observed_at="2026-06-17T12:00:00+00:00",
                **extra,
            ),
            {"tz_offset": "+00:00"},
        ),
        (
            lambda **extra: Budget(
                currency="USD",
                daily_amount="3.00",
                bid_amount="5.00",
                **extra,
            ),
            {"daily_cents": 300},
        ),
    ],
)
def test_internal_config_rejects_legacy_nested_fields(model, legacy_field):
    with pytest.raises(ValueError):
        model(**legacy_field)


# ---------------------- валидация бюджета ----------------------


# Нулевая сумма отклоняется до построения Graph payload.
def test_budget_zero_rejected():
    with pytest.raises(ValueError):
        Budget(currency="USD", daily_amount="0", bid_amount="5")


# Дневной бюджет выше hard-cap отклоняется (защита от лишнего нуля).
def test_budget_daily_hard_cap():
    with pytest.raises(ValueError):
        Budget(
            currency="USD",
            daily_amount=str(MAX_DAILY_BUDGET + Decimal("0.01")),
            bid_amount="5",
        )


# Дневной бюджет ровно на границе hard-cap проходит.
def test_budget_daily_at_cap_ok():
    b = Budget(currency="USD", daily_amount=str(MAX_DAILY_BUDGET), bid_amount="5")
    assert b.daily_amount == "100000.00"


# Lifetime-бюджет выше hard-cap отклоняется.
def test_budget_lifetime_hard_cap():
    with pytest.raises(ValueError):
        Budget(
            currency="USD",
            daily_amount="3",
            lifetime_amount=str(MAX_LIFETIME_BUDGET + Decimal("0.01")),
            bid_amount="5",
        )


# Capped-стратегия без bid_amount отклоняется.
def test_budget_capped_strategy_requires_bid():
    with pytest.raises(ValueError):
        Budget(currency="USD", daily_amount="3", bid_strategy="COST_CAP")


# Дефолтная стратегия — COST_CAP (SOP) и без bid_amount она же отклоняется.
def test_budget_default_strategy_is_cost_cap():
    assert Budget(currency="USD", daily_amount="3", bid_amount="5").bid_strategy == "COST_CAP"
    with pytest.raises(ValueError):
        Budget(currency="USD", daily_amount="3")


# Неизвестный уровень бюджета отклоняется.
def test_budget_level_invalid():
    with pytest.raises(ValueError):
        Budget(currency="USD", daily_amount="3", level="weird", bid_amount="5")


# ---------------------- валидация таргета (+AQ) ----------------------


# Антарктида (AQ) добавляется автоматически по SOP.
def test_targeting_adds_antarctica():
    t = Targeting(countries=["GH"])
    assert "AQ" in t.geo_countries()
    assert t.geo_countries() == ["GH", "AQ"]


# Если AQ уже в списке — дубля нет.
def test_targeting_no_duplicate_antarctica():
    t = Targeting(countries=["GH", "AQ"])
    assert t.geo_countries().count("AQ") == 1


# Отключение add_antarctica убирает авто-AQ.
def test_targeting_antarctica_optional():
    t = Targeting(countries=["GH"], add_antarctica=False)
    assert "AQ" not in t.geo_countries()


# ---------------------- дефолт даты ----------------------


# Внутренний run-конфиг всегда содержит уже разрешённую сервером дату.
def test_internal_start_date_is_required():
    with pytest.raises(ValueError):
        _config(start_date=None)


# Явный start_date сохраняется как есть.
def test_start_date_explicit_preserved():
    cfg = _config(start_date="2026-06-18")
    assert cfg.start_date == "2026-06-18"


# date_label форматирует дату в DD.MM для имени кампании.
def test_date_label_format():
    cfg = _config(start_date="2026-06-18")
    assert cfg.date_label == "18.06"


# start_time вычисляет offset по IANA-правилам на целевую дату.
def test_start_time_uses_iana_timezone_rules():
    cfg = _config()
    assert cfg.start_time == "2026-06-18T00:00:00-04:00"


def test_start_time_changes_across_dst_without_manual_offset():
    assert _config(start_date="2026-01-18").start_time == "2026-01-18T00:00:00-05:00"
    assert _config(start_date="2026-06-18").start_time == "2026-06-18T00:00:00-04:00"


# Legacy launch_state не игнорируется и не переводится в новый контракт неявно.
def test_legacy_launch_state_rejected():
    with pytest.raises(ValueError):
        _config(launch_state="campaign_paused")


# ---------------------- нейминг ----------------------


# render_name подставляет byer/offer/date в шаблон.
def test_render_name_substitutes():
    out = render_name(
        "{byer} | {offer} | static | adset.pro | {date}",
        byer="MV",
        offer="GH_CR",
        date_label="18.06",
    )
    assert out == "MV | GH_CR | static | adset.pro | 18.06"


# creative_codes генерирует OFFER_CRxxx с нумерацией от 1.
def test_creative_codes_sequence():
    codes = creative_codes("GH_CR", count=3)
    assert codes == ["GH_CR_CR001", "GH_CR_CR002", "GH_CR_CR003"]


# Кастомный префикс кодов поддерживается.
def test_creative_codes_custom_prefix():
    codes = creative_codes("GH_CR", count=2, prefix="GH_CR_X")
    assert codes == ["GH_CR_X001", "GH_CR_X002"]


# ---------------------- build_campaign_spec ----------------------


# Спек содержит ровно столько кампаний, сколько задано в конфиге.
def test_spec_campaign_count():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    assert isinstance(spec, CampaignSpec)
    assert len(spec.campaigns) == 1


# Число adset'ов в спеке совпадает с конфигом.
def test_spec_adset_count():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    assert len(spec.campaigns[0].adsets) == 2


# Имена кампании и adset'ов отрендерены (без плейсхолдеров).
def test_spec_names_rendered():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    camp = spec.campaigns[0]
    assert camp.name == "MV | GH_CR | static | adset.pro | 18.06"
    assert camp.adsets[0].name == "MV | GH_CR | static | s1 | 18.06"
    assert "{" not in camp.name


# Один реальный concept_ref → adset i = 1 ad. copies_per_concept в раскладку
# spec'а не вмешивается.
def test_spec_copies_default_equals_adset_count():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    # 2 adset'а блока → reported copies = 2; K=1 → по 1 ad на adset.
    assert spec.copies_per_concept == 2
    for adset in spec.campaigns[0].adsets:
        assert len(adset.ads) == 1


# concept_refs определяет истинную раскладку K×N: каждый adset = K ads.
def test_spec_uses_concept_refs_for_ads_per_adset():
    cfg = _config(campaigns=[_image_block(["a.jpg", "b.jpg", "c.jpg"])])
    spec = build_campaign_spec(cfg)
    # 2 adset'а × 3 концепта → у каждого adset 3 ad-слота.
    for adset in spec.campaigns[0].adsets:
        assert len(adset.ads) == 3


# copies_per_concept в конфиге НЕ раздувает раскладку spec'а: adset'ы spec'а всегда
# соответствуют block.adsets 1:1 (исполнитель берёт copies=len(spec.adsets)).
def test_spec_copies_per_concept_does_not_inflate_layout():
    cfg = _config(copies_per_concept=3)
    spec = build_campaign_spec(cfg)
    assert len(spec.campaigns[0].adsets) == 2  # = block.adsets, не copies_per_concept
    for adset in spec.campaigns[0].adsets:
        assert len(adset.ads) == 1  # K=1


# Раскладка per-block: concept_refs задаёт K по каждому блоку отдельно.
def test_spec_copies_default_per_block():
    one_adset = CampaignBlock(
        key="video",
        name="{byer} | {offer} | video | adset.pro | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | video | s1 | {date}", dir="c2/a1", glob="*.mp4")
        ],
        concept_refs=["v.mp4"],
    )
    cfg = _config(campaigns=[_image_block(["a.jpg", "b.jpg"]), one_adset])  # 2 adset'а / 1 adset
    spec = build_campaign_spec(cfg)
    # image-блок: K=2 → 2 ads на adset
    assert all(len(a.ads) == 2 for a in spec.campaigns[0].adsets)
    # video-блок: K=1 → 1 ad на adset
    assert all(len(a.ads) == 1 for a in spec.campaigns[1].adsets)


# Код креатива = код КОНЦЕПТА, общий по adset'ам: K=2 концепта → CR001..CR002, и
# КАЖДЫЙ adset несёт один набор [CR001, CR002] (одинаковый креатив = один код/имя).
def test_spec_ad_codes_naming():
    cfg = _config(campaigns=[_image_block(["a.jpg", "b.jpg"])])
    spec = build_campaign_spec(cfg)
    codes_by_adset = [[ad.code for ad in adset.ads] for adset in spec.campaigns[0].adsets]
    assert all(row == ["GH_CR_CR001", "GH_CR_CR002"] for row in codes_by_adset)
    flat = {c for row in codes_by_adset for c in row}
    assert flat == {"GH_CR_CR001", "GH_CR_CR002"}  # 2 различных кода по концептам


# ---------------------- canonical all-paused status ----------------------


def test_spec_status_all_paused():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    camp = spec.campaigns[0]
    assert spec.creation_policy == "all_paused"
    assert camp.status == "PAUSED"
    assert camp.body["status"] == "PAUSED"
    assert camp.adsets[0].status == "PAUSED"
    assert camp.adsets[0].body["status"] == "PAUSED"
    assert camp.adsets[0].ads[0].status == "PAUSED"


# ---------------------- маппинг тел объектов ----------------------


# CBO: бюджет/стратегия — на кампании; cap (bid_amount) — на adset (поле adset'а в Meta).
def test_spec_cbo_budget_on_campaign():
    cfg = _config(
        budget=Budget(
            level="campaign",
            currency="USD",
            daily_amount="3.00",
            bid_amount="5.00",
        )
    )
    spec = build_campaign_spec(cfg)
    camp = spec.campaigns[0]
    assert camp.body["daily_budget"] == 300
    assert camp.body["bid_strategy"] == "COST_CAP"
    # bid_amount НЕ на кампании: Meta его там игнорит → adset без cap падает Invalid parameter.
    assert "bid_amount" not in camp.body
    adset = camp.adsets[0].body
    assert "daily_budget" not in adset
    # cap едет на adset (без него COST_CAP adset = Invalid parameter, subcode 1815857).
    assert adset["bid_amount"] == 500


# ABO: бюджет/стратегия/cap — всё на adset, не на кампании.
def test_spec_abo_budget_on_adset():
    cfg = _config(
        budget=Budget(
            level="adset",
            currency="USD",
            daily_amount="3.00",
            bid_amount="5.00",
        )
    )
    spec = build_campaign_spec(cfg)
    camp = spec.campaigns[0]
    assert "daily_budget" not in camp.body
    assert "bid_amount" not in camp.body
    adset = camp.adsets[0].body
    assert adset["daily_budget"] == 300
    assert adset["bid_strategy"] == "COST_CAP"
    assert adset["bid_amount"] == 500


# Тело кампании несёт objective и special_ad_categories.
def test_spec_campaign_body_fields():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    body = spec.campaigns[0].body
    assert body["objective"] == "OUTCOME_SALES"
    assert body["special_ad_categories"] == ["NONE"]
    assert body["status"] == "PAUSED"


# Тело adset несёт гео с авто-AQ и promoted_object с pixel/event.
def test_spec_adset_body_targeting():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    body = spec.campaigns[0].adsets[0].body
    assert body["targeting"]["geo_locations"]["countries"] == ["GH", "AQ"]
    assert body["promoted_object"]["pixel_id"] == "222"
    assert body["promoted_object"]["custom_event_type"] == "PURCHASE"
    assert body["start_time"] == "2026-06-18T00:00:00-04:00"


def test_spec_adset_body_maps_explicit_gender_and_placements() -> None:
    cfg = _config(
        targeting=Targeting(
            countries=["GH"],
            genders=["female", "male"],
            placements=["facebook", "instagram"],
        )
    )

    targeting = build_campaign_spec(cfg).campaigns[0].adsets[0].body["targeting"]

    assert targeting["genders"] == [2, 1]
    assert targeting["publisher_platforms"] == ["facebook", "instagram"]


def test_spec_adset_body_omits_automatic_gender_and_placements() -> None:
    targeting = build_campaign_spec(_config()).campaigns[0].adsets[0].body["targeting"]

    assert "genders" not in targeting
    assert "publisher_platforms" not in targeting


# Advantage+ Audience форсит age_max=65 (age_max<65 + advantage → Invalid parameter 1870189).
def test_spec_adset_age_max_forced_65_under_advantage():
    cfg = _config(
        targeting=Targeting(countries=["GH"], age_min=21, age_max=55, advantage_audience=True)
    )
    body = build_campaign_spec(cfg).campaigns[0].adsets[0].body
    assert body["targeting"]["age_max"] == 65
    assert body["targeting"]["targeting_automation"]["advantage_audience"] == 1


# Без Advantage+ кастомный age_max сохраняется.
def test_spec_adset_age_max_preserved_without_advantage():
    cfg = _config(
        targeting=Targeting(countries=["GH"], age_min=21, age_max=55, advantage_audience=False)
    )
    body = build_campaign_spec(cfg).campaigns[0].adsets[0].body
    assert body["targeting"]["age_max"] == 55
    assert body["targeting"]["targeting_automation"]["advantage_audience"] == 0


# url_tags ad-слота сохраняет sub2..sub7 по SOP и добавляет stable ad id.
def test_spec_url_tags():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    ad = spec.campaigns[0].adsets[0].ads[0]
    assert ad.url_tags.startswith("sub2=MV")
    assert "sub3=GH_CR_CR001" in ad.url_tags
    assert "sub4=123456789" in ad.url_tags
    assert "sub5={{campaign.name}}" in ad.url_tags
    assert "sub6={{adset.name}}" in ad.url_tags
    assert "sub7={{ad.name}}" in ad.url_tags
    assert "sub8={{ad.id}}" in ad.url_tags


# В кастомный template sub8 добавляется ровно один раз, остальная строка не переписывается.
@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("utm_source=custom", "utm_source=custom&sub8={{ad.id}}"),
        ("utm_source=custom&sub8={{ad.id}}", "utm_source=custom&sub8={{ad.id}}"),
        ("utm_source=sub8=value", "utm_source=sub8=value&sub8={{ad.id}}"),
    ],
)
def test_spec_custom_url_tags_ensures_sub8_once(template: str, expected: str):
    cfg = _config(url_tags_template=template)
    ad = build_campaign_spec(cfg).campaigns[0].adsets[0].ads[0]
    assert ad.url_tags == expected
    query_keys = [part.split("=", 1)[0] for part in ad.url_tags.split("&")]
    assert query_keys.count("sub8") == 1


# Смешанный блок (2 концепта разного типа × 2 adset) строится без поля kind.
# Каждый adset получает 2 ad-слота, у CampaignSpec_Block атрибута kind нет.
def test_spec_mixed_block():
    block = CampaignBlock(
        key="c1",
        name="{byer} | {offer} | mixed | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | as1 | {date}", dir=".", glob="*"),
            AdsetConfig(name="{byer} | {offer} | as2 | {date}", dir=".", glob="*"),
        ],
        concept_refs=["a.jpg", "b.mp4"],
    )
    cfg = _config(campaigns=[block])
    spec = build_campaign_spec(cfg)
    assert len(spec.campaigns[0].adsets) == 2
    assert all(len(a.ads) == 2 for a in spec.campaigns[0].adsets)
    assert not hasattr(spec.campaigns[0], "kind")


def test_spec_rejects_block_without_concepts():
    cfg = _config(campaigns=[_image_block([])])
    with pytest.raises(ValueError, match="has no concept_refs"):
        build_campaign_spec(cfg)


# ---------------------- execute-скелет (порядок шагов) ----------------------


# Порядок шагов исполнения строго campaign → adsets → upload → creatives → ads.
def test_execution_steps_order():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    steps = plan_execution_steps(spec)
    kinds = [s.kind for s in steps]
    assert kinds == list(EXEC_STEP_ORDER)


def test_execution_created_objects_all_paused():
    cfg = _config()
    spec = build_campaign_spec(cfg)
    steps = plan_execution_steps(spec)
    statuses = {s.kind: s.status for s in steps}
    assert statuses["campaign"] == "PAUSED"
    assert statuses["adsets"] == "PAUSED"
    assert statuses["ads"] == "PAUSED"


# Две кампании → два полных набора шагов (по 5 на кампанию).
def test_execution_steps_per_campaign():
    second = CampaignBlock(
        key="video",
        name="{byer} | {offer} | video | adset.pro | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | video | s1 | {date}", dir="c2/a1", glob="*.mp4")
        ],
        concept_refs=["v.mp4"],
    )
    cfg = _config(campaigns=[_image_block(), second])
    spec = build_campaign_spec(cfg)
    steps = plan_execution_steps(spec)
    assert len(steps) == 10
    assert {s.campaign_key for s in steps} == {"static", "video"}
