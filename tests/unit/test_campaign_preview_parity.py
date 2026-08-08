# -*- coding: utf-8 -*-
"""Unit-тесты parity превью (build_campaign_spec) и исполнителя (build_uniquification_plan).

HIGH-1 (money): превью, по которому байер апрувит залив, должно показывать ТУ ЖЕ
раскладку, что реально создаст исполнитель.

Модель кодов: ОДИН код на концепт, ОБЩИЙ для всех его вариантов (adset'ов). Концепт c
получает code[c] в КАЖДОМ adset'е → одинаковые креативы в разных adset'ах носят один
код/имя (трекер агрегирует статистику по sub3). Уникализация (разные пиксели/md5) идёт
отдельно по seed(copy_index) и на код НЕ влияет. span блока = число концептов K.
"""

from __future__ import annotations

import pytest

from core.campaign_builder import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
    build_campaign_spec,
)
from core.campaign_builder.uniquify import ConceptInput, build_uniquification_plan

# ---------------------- фикстуры ----------------------


def _account() -> Account:
    return Account(
        act_id="123456789",
        page_id="111",
        pixel_id="222",
        timezone_name="America/New_York",
        currency="USD",
        account_context_observed_at="2026-06-17T12:00:00+00:00",
    )


def _image_block(n_adsets: int, concept_count: int = 1) -> CampaignBlock:
    """Image-кампания с n_adsets adset'ами."""
    adsets = [
        AdsetConfig(name="{byer} | {offer} | static | s%d | {date}" % i, dir=f"a{i}", glob="*.jpg")
        for i in range(1, n_adsets + 1)
    ]
    return CampaignBlock(
        key="static",
        name="{byer} | {offer} | static | adset.pro | {date}",
        adsets=adsets,
        concept_refs=[f"c{i}.jpg" for i in range(concept_count)],
    )


def _config(block: CampaignBlock, **overrides) -> CampaignConfig:
    base = dict(
        account=_account(),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
        budget=Budget(currency="USD", daily_amount="3.00", bid_amount="5.00"),
        targeting=Targeting(countries=["GH"]),
        campaigns=[block],
    )
    base.update(overrides)
    return CampaignConfig(**base)


def _concepts(count: int) -> list[ConceptInput]:
    return [
        ConceptInput(concept_id=f"c{i}", kind="image", content=b"raw-%d" % i, filename=f"c{i}.jpg")
        for i in range(count)
    ]


def _spec_codes_by_adset(block_spec) -> list[list[str]]:
    """Коды креативов превью в порядке adset → ads."""
    return [[ad.code for ad in adset.ads] for adset in block_spec.adsets]


def _plan_codes_by_adset(plan) -> list[list[str]]:
    """Коды креативов исполнителя в порядке adset → ads."""
    return [[ad.code for ad in adset.ads] for adset in plan.adsets]


# ---------------------- основной кейс: 3 концепта × 2 adset ----------------------


# 3 концепта × 2 adset: 6 ads, но кодов 3 (по концепту), и КАЖДЫЙ adset несёт один и тот
# же набор CR001..CR003 — одинаковый креатив в разных adset'ах = один код/имя.
def test_preview_3concepts_2adsets_ad_count_and_shared_codes():
    block = _image_block(n_adsets=2, concept_count=3)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)

    block_spec = spec.campaigns[0]
    all_codes = [ad.code for adset in block_spec.adsets for ad in adset.ads]
    # K×N = 3×2 = 6 ads всего.
    assert len(all_codes) == 6
    # Но РАЗЛИЧНЫХ кодов = K = 3 (код общий между adset'ами для одного концепта).
    assert len(set(all_codes)) == 3
    assert sorted(set(all_codes)) == [f"GH_CR_CR{i:03d}" for i in range(1, 4)]
    # adset i = K ads (по 1 на концепт), и оба adset'а несут ОДИН набор кодов.
    codes_by_adset = _spec_codes_by_adset(block_spec)
    assert all(row == ["GH_CR_CR001", "GH_CR_CR002", "GH_CR_CR003"] for row in codes_by_adset)


# Превью и исполнитель дают ПОБИТОВО ту же раскладку кодов по adset'ам (parity).
def test_preview_matches_executor_layout_3x2():
    block = _image_block(n_adsets=2, concept_count=3)
    cfg = _config(block)
    concepts = _concepts(3)

    spec = build_campaign_spec(cfg)
    plan = build_uniquification_plan(cfg, block, concepts, copies=2)

    assert _spec_codes_by_adset(spec.campaigns[0]) == _plan_codes_by_adset(plan)


# ---------------------- параметрический parity ----------------------


# При любом K концептов × N adset'ов превью совпадает с исполнителем побитово; кодов = K,
# каждый adset несёт один и тот же набор CR001..CR_K.
@pytest.mark.parametrize(
    ("k_concepts", "n_adsets"),
    [(1, 1), (1, 3), (2, 2), (3, 2), (2, 4), (4, 1), (3, 3)],
)
def test_preview_executor_parity_parametric(k_concepts: int, n_adsets: int):
    block = _image_block(n_adsets=n_adsets, concept_count=k_concepts)
    cfg = _config(block)
    concepts = _concepts(k_concepts)

    spec = build_campaign_spec(cfg)
    plan = build_uniquification_plan(cfg, block, concepts, copies=n_adsets)

    spec_layout = _spec_codes_by_adset(spec.campaigns[0])
    plan_layout = _plan_codes_by_adset(plan)

    # Совпадение раскладки кодов по adset'ам.
    assert spec_layout == plan_layout
    # Всего K×N ads, но РАЗЛИЧНЫХ кодов = K (код общий по adset'ам для одного концепта).
    flat = [c for row in spec_layout for c in row]
    assert len(flat) == k_concepts * n_adsets
    expected_codes = [f"GH_CR_CR{i:03d}" for i in range(1, k_concepts + 1)]
    assert sorted(set(flat)) == expected_codes
    # Каждый adset несёт ровно K ads и ОДИН и тот же набор кодов концептов.
    assert all(row == expected_codes for row in spec_layout)


# ---------------------- multi-block concept_refs ----------------------


# concept_refs задаёт K по каждому блоку; нумерация СКВОЗНАЯ по концептам всего залива
# (блок B продолжает с номера после концептов блока A) — sub3=CRxxx глобально уникален.
def test_concept_counts_per_block():
    block_a = _image_block(n_adsets=2, concept_count=2)
    block_b = CampaignBlock(
        key="video",
        name="{byer} | {offer} | video | adset.pro | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | video | s1 | {date}", dir="v1", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s2 | {date}", dir="v2", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s3 | {date}", dir="v3", glob="*.mp4"),
        ],
        concept_refs=["v.mp4"],
    )
    cfg = _config(block_a, campaigns=[block_a, block_b])
    spec = build_campaign_spec(cfg)

    static_spec, video_spec = spec.campaigns
    # static: K=2 концепта × N=2 adset = 4 ads, но 2 кода CR001..CR002 (общие по adset).
    static_codes = [ad.code for adset in static_spec.adsets for ad in adset.ads]
    assert len(static_codes) == 4
    assert sorted(set(static_codes)) == [f"GH_CR_CR{i:03d}" for i in range(1, 3)]
    # video: K=1 концепт → ПРОДОЛЖАЕТ нумерацию с CR003 (span static = 2 концепта).
    video_codes = [ad.code for adset in video_spec.adsets for ad in adset.ads]
    assert len(video_codes) == 3  # 1 концепт × 3 adset
    assert set(video_codes) == {"GH_CR_CR003"}
    # Коды концептов двух блоков глобально различны: {CR001, CR002, CR003}.
    assert set(static_codes + video_codes) == {"GH_CR_CR001", "GH_CR_CR002", "GH_CR_CR003"}


# Кросс-блок parity: исполнитель (накапливает code_start по числу КОНЦЕПТОВ блока)
# даёт ровно те коды по блокам, что превью build_campaign_spec.
def test_cross_block_executor_parity():
    block_a = _image_block(n_adsets=2, concept_count=2)
    block_b = CampaignBlock(
        key="video",
        name="{byer} | {offer} | video | adset.pro | {date}",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | video | s1 | {date}", dir="v1", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s2 | {date}", dir="v2", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s3 | {date}", dir="v3", glob="*.mp4"),
        ],
        concept_refs=["v.mp4"],
    )
    cfg = _config(block_a, campaigns=[block_a, block_b])
    concepts_a = _concepts(2)  # 2 концепта на static
    concepts_b = _concepts(1)  # 1 концепт на video

    spec = build_campaign_spec(cfg)

    # Исполнитель накапливает code_start: блок B стартует с block_code_span(2 концепта)+1 = 3.
    plan_a = build_uniquification_plan(cfg, block_a, concepts_a, copies=2, code_start=1)
    plan_b = build_uniquification_plan(cfg, block_b, concepts_b, copies=3, code_start=3)

    assert _spec_codes_by_adset(spec.campaigns[0]) == _plan_codes_by_adset(plan_a)
    assert _spec_codes_by_adset(spec.campaigns[1]) == _plan_codes_by_adset(plan_b)


# ---------------------- single concept source ----------------------


# Один concept_ref даёт один ad с кодом CR001 в каждом adset.
def test_single_concept_ref_produces_one_ad_per_adset():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)

    block_spec = spec.campaigns[0]
    codes_by_adset = _spec_codes_by_adset(block_spec)
    # 1 концепт × 3 adset = 3 ads, по 1 на adset, код CR001 во всех.
    assert all(len(row) == 1 for row in codes_by_adset)
    flat = [c for row in codes_by_adset for c in row]
    assert flat == ["GH_CR_CR001", "GH_CR_CR001", "GH_CR_CR001"]
    assert set(flat) == {"GH_CR_CR001"}


# Превью совпадает с исполнителем при одном concept_ref.
def test_single_concept_ref_matches_executor():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    concepts = _concepts(1)

    spec = build_campaign_spec(cfg)
    plan = build_uniquification_plan(cfg, block, concepts, copies=3)

    assert _spec_codes_by_adset(spec.campaigns[0]) == _plan_codes_by_adset(plan)


# Число adset-слотов раскладки = числу adset'ов блока (executor: copies=len(spec.adsets)),
# а не copies_per_concept. Превью из concept_refs зеркалит тот же выбор copies.
def test_layout_copies_equals_block_adset_count():
    block = _image_block(n_adsets=2, concept_count=2)
    # copies_per_concept=4 в конфиге не должен раздуть раскладку: executor берёт
    # copies=len(spec.adsets)=2, превью обязано совпасть.
    cfg = _config(block, copies_per_concept=4)
    concepts = _concepts(2)

    spec = build_campaign_spec(cfg)
    plan = build_uniquification_plan(cfg, block, concepts, copies=len(block.adsets))

    spec_layout = _spec_codes_by_adset(spec.campaigns[0])
    plan_layout = _plan_codes_by_adset(plan)
    assert spec_layout == plan_layout
    flat = [c for row in spec_layout for c in row]
    assert len(flat) == 2 * 2  # K концептов × N adset'ов = 4 ads
    assert sorted(set(flat)) == [f"GH_CR_CR{i:03d}" for i in range(1, 3)]  # 2 кода
    assert all(row == ["GH_CR_CR001", "GH_CR_CR002"] for row in spec_layout)
