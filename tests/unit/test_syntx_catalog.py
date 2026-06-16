# -*- coding: utf-8 -*-
"""Unit: ModelCatalog — склейка /ai + /ai/models и выборки по scope/ai_name."""

from __future__ import annotations

from core.syntx.catalog import ModelCatalog

_AI = [
    {"value": "sora-images", "scope": "image"},
    {"value": "banana", "scope": "image"},
    {"value": "kling", "scope": "video"},
    {"value": "suno", "scope": "audio"},
]
_MODELS = [
    {"value": "gpt-image-2", "ai_name": "sora-images", "default": True, "settings": {}},
    {"value": "gpt-image-1", "ai_name": "sora-images", "default": False, "settings": {}},
    {"value": "banana3", "ai_name": "banana", "default": True, "settings": {}},
    {"value": "kling_image2video", "ai_name": "kling", "default": False, "settings": {}},
    {"value": "kling_text2video", "ai_name": "kling", "default": True, "settings": {}},
]


def _cat() -> ModelCatalog:
    return ModelCatalog.from_api(_AI, _MODELS)


# scope из /ai прокидывается в модели из /ai/models (в /ai/models его нет).
def test_from_api_injects_scope() -> None:
    cat = _cat()
    gpt = cat.find("sora-images", "gpt-image-2")
    assert gpt is not None and gpt.scope == "image"
    kling = cat.find("kling", "kling_image2video")
    assert kling is not None and kling.scope == "video"


# for_scope/image_models/video_models фильтруют по scope.
def test_scope_filters() -> None:
    cat = _cat()
    assert {m.model_type for m in cat.image_models()} == {"gpt-image-2", "gpt-image-1", "banana3"}
    assert {m.model_type for m in cat.video_models()} == {"kling_image2video", "kling_text2video"}


# default_for возвращает модель с флагом default, иначе первую.
def test_default_for() -> None:
    cat = _cat()
    assert cat.default_for("sora-images").model_type == "gpt-image-2"
    assert cat.default_for("kling").model_type == "kling_text2video"
    assert cat.default_for("unknown") is None


# find на несуществующей модели → None.
def test_find_missing() -> None:
    assert _cat().find("sora-images", "nope") is None


# ai_names_for_scope перечисляет ai по разделу (включая те, что без моделей).
def test_ai_names_for_scope() -> None:
    cat = _cat()
    assert set(cat.ai_names_for_scope("image")) == {"sora-images", "banana"}
    assert set(cat.ai_names_for_scope("audio")) == {"suno"}


# Пустые payload'ы не валят парсер.
def test_from_api_empty() -> None:
    cat = ModelCatalog.from_api(None, None)
    assert cat.models == ()
    assert cat.image_models() == ()
