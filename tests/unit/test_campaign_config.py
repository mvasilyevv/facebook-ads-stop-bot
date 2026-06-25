# -*- coding: utf-8 -*-
"""Юнит-тесты для CampaignBlock (конфиг создания кампании)."""

import pytest

from core.campaign_builder.config import AdsetConfig, CampaignBlock


def _adset() -> AdsetConfig:
    return AdsetConfig(name="as", dir=".", glob="*")


# Смешанный блок (фото+видео) проходит валидацию; kind не нужен.
def test_mixed_block_is_valid():
    block = CampaignBlock(
        key="c1",
        name="C1",
        adsets=[_adset()],
        concept_refs=["a.jpg", "b.mp4"],
    )
    assert block.concept_refs == ["a.jpg", "b.mp4"]


# Файл с неизвестным расширением должен быть отклонён с ValueError.
def test_unknown_extension_rejected():
    with pytest.raises(ValueError, match="неизвестн"):
        CampaignBlock(key="c1", name="C1", adsets=[_adset()], concept_refs=["a.txt"])
