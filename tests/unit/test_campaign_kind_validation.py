# -*- coding: utf-8 -*-
"""Money-safety: концепт чужого типа в кампании отклоняется ДО любого Meta-POST.

Без этого видео-концепт в image-кампании (или наоборот) роняет уникализатор
(PIL на mp4 / ffmpeg на jpg) уже ПОСЛЕ создания кампании+adset'ов в Meta →
орфаны + слив. Валидатор CampaignBlock ловит это на этапе валидации конфига
(срабатывает в validate, launch и при загрузке конфига воркером).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.campaign_builder.config import CampaignConfig, ref_media_kind


def _cfg(block_kind: str, refs: list[str]) -> dict:
    return {
        "account": {"act_id": "1", "page_id": "1", "pixel_id": "1", "tz_offset": "-07:00"},
        "offer_code": "X",
        "destination_link": "https://e.com",
        "start_date": "2026-07-01",
        "targeting": {"countries": ["DE"]},
        "campaigns": [
            {
                "key": "k",
                "name": "n",
                "kind": block_kind,
                "adsets": [{"name": "a", "dir": "d", "glob": "*"}],
                "concept_refs": refs,
            }
        ],
    }


# Видео-ref в image-кампании → ValidationError (иначе орфаны в Meta).
def test_video_ref_in_image_block_rejected():
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate(_cfg("image", ["promo.mp4"]))


# Фото-ref в video-кампании → ValidationError.
def test_image_ref_in_video_block_rejected():
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate(_cfg("video", ["banner.jpg"]))


# Совпадающий тип → ок.
def test_matching_kind_ok():
    CampaignConfig.model_validate(_cfg("image", ["a.jpg", "b.png"]))
    CampaignConfig.model_validate(_cfg("video", ["v.mp4", "w.mov"]))


# Неизвестное расширение не отклоняется — воркер проверит реальный файл.
def test_unknown_ext_allowed():
    CampaignConfig.model_validate(_cfg("image", ["noext"]))


# ref_media_kind: по расширению.
def test_ref_media_kind():
    assert ref_media_kind("a.MP4") == "video"
    assert ref_media_kind("b.JPG") == "image"
    assert ref_media_kind("c.bin") is None
    assert ref_media_kind("noext") is None
