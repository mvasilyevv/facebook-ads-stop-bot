# -*- coding: utf-8 -*-
"""Unit-тесты build_uniquification_plan и uniquify_concepts (без БД, без сети).

Покрывают: media_kind per-concept при смешанных медиа (фото + видео в одном блоке).
"""

from __future__ import annotations

from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)
from core.campaign_builder.uniquify import ConceptInput, build_uniquification_plan

# ---------------------- локальная фабрика конфига ----------------------


def _account() -> Account:
    """Минимальный валидный кабинет."""
    return Account(act_id="123456789", page_id="111", pixel_id="222")


def _config(**overrides) -> CampaignConfig:
    """Собирает минимальный валидный CampaignConfig для unit-тестов."""
    base = dict(
        account=_account(),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
        creo_root="~/Documents/FB_Agent_Creo/CR_GH",
        budget=Budget(daily_cents=300, bid_amount_cents=500),
        targeting=Targeting(countries=["GH"]),
        campaigns=[],
    )
    base.update(overrides)
    return CampaignConfig(**base)


# ---------------------- тесты build_uniquification_plan ----------------------


# В одном блоке фото и видео → media_kind вариантов берётся из типа концепта.
def test_plan_media_kind_per_concept():
    block = CampaignBlock(
        key="c1",
        name="C1",
        adsets=[AdsetConfig(name="as1", dir=".", glob="*")],
        concept_refs=["a.jpg", "b.mp4"],
    )
    cfg = _config(campaigns=[block])
    concepts = [
        ConceptInput(concept_id="c1:0:a", kind="image", content=b"x", filename="a.jpg"),
        ConceptInput(concept_id="c1:1:b", kind="video", path="/tmp/b.mp4", filename="b.mp4"),
    ]
    plan = build_uniquification_plan(cfg, block, concepts, copies=1)
    kinds = {ad.concept_id: ad.media_kind for ad in plan.adsets[0].ads}
    assert kinds["c1:0:a"] == "image"
    assert kinds["c1:1:b"] == "video"


# Только фото-концепты → все варианты image.
def test_plan_all_image_concepts():
    block = CampaignBlock(
        key="img",
        name="IMG",
        adsets=[
            AdsetConfig(name="as1", dir=".", glob="*"),
            AdsetConfig(name="as2", dir=".", glob="*"),
        ],
        concept_refs=["a.jpg", "c.png"],
    )
    cfg = _config(campaigns=[block])
    concepts = [
        ConceptInput(concept_id="img:0:a", kind="image", content=b"x", filename="a.jpg"),
        ConceptInput(concept_id="img:1:c", kind="image", content=b"y", filename="c.png"),
    ]
    plan = build_uniquification_plan(cfg, block, concepts, copies=2)
    for adset in plan.adsets:
        for ad in adset.ads:
            assert ad.media_kind == "image", f"ожидался image, получен {ad.media_kind}"


# Только видео-концепты → все варианты video.
def test_plan_all_video_concepts():
    block = CampaignBlock(
        key="vid",
        name="VID",
        adsets=[
            AdsetConfig(name="as1", dir=".", glob="*"),
        ],
        concept_refs=["x.mp4"],
    )
    cfg = _config(campaigns=[block])
    concepts = [
        ConceptInput(concept_id="vid:0:x", kind="video", path="/tmp/x.mp4", filename="x.mp4"),
    ]
    plan = build_uniquification_plan(cfg, block, concepts, copies=1)
    assert plan.adsets[0].ads[0].media_kind == "video"


# Несколько копий смешанного блока → media_kind не меняется между копиями.
def test_plan_mixed_multiple_copies_consistent():
    block = CampaignBlock(
        key="mix",
        name="MIX",
        adsets=[
            AdsetConfig(name="as1", dir=".", glob="*"),
            AdsetConfig(name="as2", dir=".", glob="*"),
        ],
        concept_refs=["a.jpg", "b.mp4"],
    )
    cfg = _config(campaigns=[block])
    concepts = [
        ConceptInput(concept_id="mix:0:a", kind="image", content=b"x", filename="a.jpg"),
        ConceptInput(concept_id="mix:1:b", kind="video", path="/tmp/b.mp4", filename="b.mp4"),
    ]
    plan = build_uniquification_plan(cfg, block, concepts, copies=2)
    # Для каждого adset'а проверяем, что типы совпадают с типом концепта
    for adset in plan.adsets:
        for ad in adset.ads:
            expected_kind = next(c.kind for c in concepts if c.concept_id == ad.concept_id)
            assert ad.media_kind == expected_kind, (
                f"adset {adset.index}, concept {ad.concept_id}: "
                f"ожидался {expected_kind}, получен {ad.media_kind}"
            )
