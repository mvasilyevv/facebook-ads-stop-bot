# -*- coding: utf-8 -*-
"""Unit: dataclass-контракты core.syntx.models (валидация + парсинг API-строк)."""

from __future__ import annotations

import pytest

from core.syntx.models import (
    SCOPE_IMAGE,
    Balance,
    GenRequest,
    ModelInfo,
    UploadedRef,
)


# GenRequest с пустым промптом → ValueError.
def test_genrequest_empty_prompt_raises() -> None:
    with pytest.raises(ValueError):
        GenRequest(scope=SCOPE_IMAGE, ai_name="sora-images", model_type="gpt-image-2", prompt="  ")


# Неизвестный scope → ValueError.
def test_genrequest_bad_scope_raises() -> None:
    with pytest.raises(ValueError):
        GenRequest(scope="hologram", ai_name="x", model_type="y", prompt="hi")


# n < 1 → ValueError.
def test_genrequest_bad_n_raises() -> None:
    with pytest.raises(ValueError):
        GenRequest(scope=SCOPE_IMAGE, ai_name="x", model_type="y", prompt="hi", n=0)


# Валидный GenRequest создаётся, дефолты проставлены.
def test_genrequest_valid_defaults() -> None:
    req = GenRequest(
        scope=SCOPE_IMAGE, ai_name="sora-images", model_type="gpt-image-2", prompt="hi"
    )
    assert req.aspect_ratio == "16:9"
    assert req.quality == "1K"
    assert req.n == 1


# ModelInfo.from_models_row парсит settings (media/limits/cost-params).
def test_modelinfo_from_models_row() -> None:
    row = {
        "value": "gpt-image-2",
        "label": "GPT Image 2",
        "ai_name": "sora-images",
        "default": True,
        "settings": {
            "allowed_media_types": ["images"],
            "accepted_file_types": ["image/jpeg"],
            "file_count_limit": 10,
            "get_cost_params": ["aspect_ratio", "quality"],
        },
    }
    info = ModelInfo.from_models_row(row)
    assert info.ai_name == "sora-images"
    assert info.model_type == "gpt-image-2"
    assert info.default is True
    assert info.allowed_media_types == ("images",)
    assert info.get_cost_params == ("aspect_ratio", "quality")


# max_files_for: int-лимит возвращается как есть.
def test_modelinfo_max_files_int() -> None:
    info = ModelInfo(ai_name="a", model_type="b", file_count_limit=5)
    assert info.max_files_for() == 5


# max_files_for: dict-лимит по типу медиа (veo_omni: images=7, videos=1).
def test_modelinfo_max_files_dict() -> None:
    info = ModelInfo(
        ai_name="veo3", model_type="veo_omni", file_count_limit={"images": 7, "videos": 1}
    )
    assert info.max_files_for("images") == 7
    assert info.max_files_for("videos") == 1
    assert info.max_files_for("audios") == 0


# UploadedRef.from_api_row достаёт url и метаданные.
def test_uploadedref_from_api_row() -> None:
    ref = UploadedRef.from_api_row(
        {"filename": "x.jpg", "url": "https://r2/u/x.jpg", "status": "uploaded", "size": 123}
    )
    assert ref.url == "https://r2/u/x.jpg"
    assert ref.size == 123


# Balance.from_api_payload приводит баланс к float.
def test_balance_from_api_payload() -> None:
    bal = Balance.from_api_payload({"balance": "1775.896", "user_id": "911436108"})
    assert bal.tokens == pytest.approx(1775.896)
    assert bal.user_id == "911436108"
