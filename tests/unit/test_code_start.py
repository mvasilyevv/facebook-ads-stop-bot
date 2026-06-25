# -*- coding: utf-8 -*-
"""Тесты code_start смещения и total_code_span (без БД, без сети).

Проверяют, что CampaignConfig.code_start сдвигает нумерацию кодов
и что total_code_span корректно суммирует диапазоны блоков.
"""

from __future__ import annotations

from core.campaign_builder.builder import build_campaign_spec, total_code_span
from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)


def _account() -> Account:
    """Минимальный валидный кабинет."""
    return Account(act_id="123456789", page_id="111", pixel_id="222")


def _block(key: str, n_adsets: int, refs: list[str]) -> CampaignBlock:
    """Кампания с заданным числом adset'ов и концептов."""
    return CampaignBlock(
        key=key,
        name=f"{key} {{offer}}",
        adsets=[AdsetConfig(name=f"as{i} {{offer}}", dir=".", glob="*") for i in range(n_adsets)],
        concept_refs=refs,
    )


def _config(**overrides) -> CampaignConfig:
    """Собирает минимальный валидный CampaignConfig с переопределениями."""
    base = dict(
        account=_account(),
        offer_code="GH",
        destination_link="https://example.shop/x",
        start_date="2026-06-25",
        budget=Budget(daily_cents=300, bid_amount_cents=500),
        targeting=Targeting(countries=["GH"]),
        campaigns=[_block("c1", 1, ["a.jpg"])],
    )
    base.update(overrides)
    return CampaignConfig(**base)


# code_start=10 → первый код должен быть GH_CR010 (смещение нумерации).
def test_code_start_offsets_codes():
    cfg = _config(
        campaigns=[_block("c1", 1, ["a.jpg"])],
        offer_code="GH",
        code_start=10,
    )
    spec = build_campaign_spec(cfg, concept_counts={"c1": 1})
    assert spec.campaigns[0].adsets[0].ads[0].code == "GH_CR010"


# total_code_span суммирует K×N по блокам: c1 2×2=4, c2 1×3=3 → 7.
def test_total_code_span():
    cfg = _config(
        campaigns=[
            _block("c1", 2, ["a.jpg", "b.mp4"]),
            _block("c2", 3, ["c.jpg"]),
        ],
        offer_code="GH",
    )
    # c1: 2 концепта × 2 adset = 4; c2: 1 концепт × 3 adset = 3 → итого 7
    assert total_code_span(cfg) == 7
