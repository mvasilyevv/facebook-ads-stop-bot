# -*- coding: utf-8 -*-
"""Unit-тесты parity превью (build_campaign_spec) и исполнителя (build_uniquification_plan).

HIGH-1 (money): превью, по которому байер апрувит залив, должно показывать ТУ ЖЕ
раскладку, что реально создаст исполнитель. Баг: build_campaign_spec считал коды
креативов per-adset (каждый adset перезапускал нумерацию с CR001) и трактовал copies
как число ads на adset — расхождение с исполнителем, где раскладка K концептов × N
adset'ов: total ads = K×N, сквозная нумерация OFFER_CRxxx, adset i = K ads (1 на концепт).

Эти тесты фиксируют единый source-of-truth раскладки и сводят обе стороны к нему.
"""

from __future__ import annotations

import pytest

from core.campaign_builder import (
    Account,
    AdsetConfig,
    CampaignBlock,
    CampaignConfig,
    Targeting,
    build_campaign_spec,
)
from core.campaign_builder.uniquify import ConceptInput, build_uniquification_plan

# ---------------------- фикстуры ----------------------


def _account() -> Account:
    return Account(act_id="123456789", page_id="111", pixel_id="222")


def _image_block(n_adsets: int) -> CampaignBlock:
    """Image-кампания с n_adsets adset'ами."""
    adsets = [
        AdsetConfig(name="{byer} | {offer} | static | s%d | {date}" % i, dir=f"a{i}", glob="*.jpg")
        for i in range(1, n_adsets + 1)
    ]
    return CampaignBlock(
        key="static",
        name="{byer} | {offer} | static | adset.pro | {date}",
        kind="image",
        adsets=adsets,
    )


def _config(block: CampaignBlock, **overrides) -> CampaignConfig:
    base = dict(
        account=_account(),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
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


# ---------------------- основной кейс HIGH-1: 3 концепта × 2 adset ----------------------


# 3 концепта × 2 adset: превью показывает ровно 6 ads с уникальными сквозными кодами
# CR001..CR006 (а не CR001..CR00x повторно в каждом adset).
def test_preview_3concepts_2adsets_ad_count_and_unique_codes():
    block = _image_block(n_adsets=2)
    cfg = _config(block)
    spec = build_campaign_spec(cfg, concept_counts={"static": 3})

    block_spec = spec.campaigns[0]
    all_codes = [ad.code for adset in block_spec.adsets for ad in adset.ads]
    # K×N = 3×2 = 6 ads всего.
    assert len(all_codes) == 6
    # Все коды уникальны (нет повторного CR001 в разных adset).
    assert len(set(all_codes)) == 6
    # Сквозная нумерация CR001..CR006.
    assert sorted(all_codes) == [f"GH_CR_CR{i:03d}" for i in range(1, 7)]
    # adset i = K ads (по 1 на концепт).
    assert all(len(adset.ads) == 3 for adset in block_spec.adsets)


# Превью и исполнитель дают ПОБИТОВО ту же раскладку кодов по adset'ам (parity).
def test_preview_matches_executor_layout_3x2():
    block = _image_block(n_adsets=2)
    cfg = _config(block)
    concepts = _concepts(3)

    spec = build_campaign_spec(cfg, concept_counts={"static": len(concepts)})
    plan = build_uniquification_plan(cfg, block, concepts, copies=2)

    assert _spec_codes_by_adset(spec.campaigns[0]) == _plan_codes_by_adset(plan)


# ---------------------- параметрический parity K×N ----------------------


# При любом K концептов × N adset'ов превью совпадает с исполнителем побитово.
@pytest.mark.parametrize(
    ("k_concepts", "n_adsets"),
    [(1, 1), (1, 3), (2, 2), (3, 2), (2, 4), (4, 1), (3, 3)],
)
def test_preview_executor_parity_parametric(k_concepts: int, n_adsets: int):
    block = _image_block(n_adsets=n_adsets)
    cfg = _config(block)
    concepts = _concepts(k_concepts)

    spec = build_campaign_spec(cfg, concept_counts={"static": k_concepts})
    plan = build_uniquification_plan(cfg, block, concepts, copies=n_adsets)

    spec_layout = _spec_codes_by_adset(spec.campaigns[0])
    plan_layout = _plan_codes_by_adset(plan)

    # Совпадение раскладки кодов по adset'ам.
    assert spec_layout == plan_layout
    # Всего K×N ads, коды уникальны и сквозные.
    flat = [c for row in spec_layout for c in row]
    assert len(flat) == k_concepts * n_adsets
    assert sorted(flat) == [f"GH_CR_CR{i:03d}" for i in range(1, k_concepts * n_adsets + 1)]
    # Каждый adset несёт ровно K ads.
    assert all(len(row) == k_concepts for row in spec_layout)


# ---------------------- multi-block concept_counts ----------------------


# concept_counts задаёт K по каждому блоку отдельно; коды каждого блока сквозные внутри блока.
def test_concept_counts_per_block():
    block_a = _image_block(n_adsets=2)
    block_b = CampaignBlock(
        key="video",
        name="{byer} | {offer} | video | adset.pro | {date}",
        kind="video",
        adsets=[
            AdsetConfig(name="{byer} | {offer} | video | s1 | {date}", dir="v1", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s2 | {date}", dir="v2", glob="*.mp4"),
            AdsetConfig(name="{byer} | {offer} | video | s3 | {date}", dir="v3", glob="*.mp4"),
        ],
    )
    cfg = _config(block_a, campaigns=[block_a, block_b])
    spec = build_campaign_spec(cfg, concept_counts={"static": 2, "video": 1})

    static_spec, video_spec = spec.campaigns
    # static: K=2 × N=2 = 4 ads.
    static_codes = [ad.code for adset in static_spec.adsets for ad in adset.ads]
    assert len(static_codes) == 4
    assert sorted(static_codes) == [f"GH_CR_CR{i:03d}" for i in range(1, 5)]
    # video: K=1 × N=3 = 3 ads.
    video_codes = [ad.code for adset in video_spec.adsets for ad in adset.ads]
    assert len(video_codes) == 3
    # Каждый блок имеет собственную сквозную нумерацию (начинается с CR001).
    assert sorted(video_codes) == [f"GH_CR_CR{i:03d}" for i in range(1, 4)]


# ---------------------- фолбэк concept_counts=None ----------------------


# Без concept_counts превью предполагает 1 концепт/блок: adset i = 1 ad, коды сквозные
# по блоку (CR001..CR_N), БЕЗ дубля CR001 в разных adset.
def test_fallback_no_concept_counts_assumes_one_concept_per_block():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)  # concept_counts=None

    block_spec = spec.campaigns[0]
    codes_by_adset = _spec_codes_by_adset(block_spec)
    # 1 концепт × 3 adset = 3 ads, по 1 на adset.
    assert all(len(row) == 1 for row in codes_by_adset)
    flat = [c for row in codes_by_adset for c in row]
    assert flat == ["GH_CR_CR001", "GH_CR_CR002", "GH_CR_CR003"]
    # Никаких дублей кодов между adset'ами.
    assert len(set(flat)) == len(flat)


# Фолбэк совпадает с исполнителем при 1 концепте на блок.
def test_fallback_matches_executor_one_concept():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    concepts = _concepts(1)

    spec = build_campaign_spec(cfg)  # фолбэк: 1 концепт
    plan = build_uniquification_plan(cfg, block, concepts, copies=3)

    assert _spec_codes_by_adset(spec.campaigns[0]) == _plan_codes_by_adset(plan)


# Число adset-слотов раскладки = числу adset'ов блока (executor: copies=len(spec.adsets)),
# а не copies_per_concept. Превью с concept_counts должно зеркалить тот же выбор copies.
def test_layout_copies_equals_block_adset_count():
    block = _image_block(n_adsets=2)
    # copies_per_concept=4 в конфиге не должен раздуть раскладку: executor берёт
    # copies=len(spec.adsets)=2, превью обязано совпасть.
    cfg = _config(block, copies_per_concept=4)
    concepts = _concepts(2)

    spec = build_campaign_spec(cfg, concept_counts={"static": 2})
    plan = build_uniquification_plan(cfg, block, concepts, copies=len(block.adsets))

    spec_layout = _spec_codes_by_adset(spec.campaigns[0])
    plan_layout = _plan_codes_by_adset(plan)
    assert spec_layout == plan_layout
    flat = [c for row in spec_layout for c in row]
    assert len(flat) == 2 * 2  # K концептов × N adset'ов
    assert sorted(flat) == [f"GH_CR_CR{i:03d}" for i in range(1, 5)]
