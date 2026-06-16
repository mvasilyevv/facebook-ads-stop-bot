# -*- coding: utf-8 -*-
"""Unit: pure-хелперы SyntxClient (settings-билдеры, парс результата, модерация)."""

from __future__ import annotations

import pytest

from core.syntx.client import SyntxClient
from core.syntx.errors import SyntxModerationError
from core.syntx.models import SCOPE_IMAGE, SCOPE_VIDEO, GenRequest


def _img_req(**kw) -> GenRequest:
    base = dict(scope=SCOPE_IMAGE, ai_name="sora-images", model_type="gpt-image-2", prompt="hi")
    base.update(kw)
    return GenRequest(**base)


# image-settings: с рефами кладётся image_url (singular), extra мёржится.
def test_build_image_settings_with_refs() -> None:
    req = _img_req(aspect_ratio="1:1", details_quality="high", extra={"seed": 7})
    s = SyntxClient._build_image_settings(req, ["https://r2/u/a.jpg"])
    assert s["model_type"] == "gpt-image-2"
    assert s["aspect_ratio"] == "1:1"
    assert s["details_quality"] == "high"
    assert s["image_url"] == ["https://r2/u/a.jpg"]
    assert s["seed"] == 7


# Без рефов ключа image_url нет (text-to-image).
def test_build_image_settings_no_refs() -> None:
    s = SyntxClient._build_image_settings(_img_req(), [])
    assert "image_url" not in s


# video-settings: image_urls PLURAL + type + video_duration (заложено на будущее).
def test_build_video_settings_plural_and_duration() -> None:
    req = GenRequest(
        scope=SCOPE_VIDEO,
        ai_name="kling",
        model_type="kling_image2video",
        prompt="animate",
        image_refs=("https://r2/u/a.jpg",),
        video_duration=5,
    )
    s = SyntxClient._build_video_settings(req, ["https://r2/u/a.jpg"])
    assert s["image_urls"] == ["https://r2/u/a.jpg"]
    assert "image_url" not in s
    assert s["video_duration"] == 5
    assert s["type"] == "references"


# _extract_generated_urls: только /generated/, дедуп, без /uploaded/ и не-image.
def test_extract_generated_urls() -> None:
    messages = [
        {
            "message_object": [
                {"object_type": "text", "object_url": ""},
                {"object_type": "image", "object_url": "https://r2/u/uploaded/ref.jpg"},
            ]
        },
        {
            "message_object": [
                {"object_type": "image", "object_url": "https://r2/u/generated/a.jpg"},
                {"object_type": "image", "object_url": "https://r2/u/generated/a.jpg"},
                {"object_type": "image", "object_url": "https://r2/u/generated/b.jpg"},
            ]
        },
    ]
    urls = SyntxClient._extract_generated_urls(messages)
    assert urls == ["https://r2/u/generated/a.jpg", "https://r2/u/generated/b.jpg"]


# _raise_if_moderation бросает на маркер в object_text.
def test_raise_if_moderation_hits() -> None:
    messages = [
        {"message_object": [{"object_type": "text", "object_text": "image_violation detected"}]}
    ]
    with pytest.raises(SyntxModerationError):
        SyntxClient._raise_if_moderation(messages, "chat-1")


# Без маркеров — молчит.
def test_raise_if_moderation_clean() -> None:
    messages = [{"message_object": [{"object_type": "text", "object_text": "ok"}]}]
    SyntxClient._raise_if_moderation(messages, "chat-1")  # не бросает


# video-генерация пока выключена → NotImplementedError (хук заложен).
@pytest.mark.asyncio
async def test_generate_video_not_implemented() -> None:
    req = GenRequest(
        scope=SCOPE_VIDEO, ai_name="kling", model_type="kling_image2video", prompt="go"
    )
    cl = SyntxClient(token="ey.fake.tok")
    with pytest.raises(NotImplementedError):
        await cl.generate_video(req)


# banana НЕ принимает quality/details_quality (иначе чёрный кадр) — только image_size.
def test_build_image_settings_banana_profile() -> None:
    req = GenRequest(
        scope=SCOPE_IMAGE,
        ai_name="banana",
        model_type="banana3",
        prompt="edit",
        quality="1K",
        details_quality="high",
        image_size="2K",
        aspect_ratio=None,
    )
    s = SyntxClient._build_image_settings(req, ["https://r2/u/a.jpg"])
    assert s["model_type"] == "banana3"
    assert s["image_size"] == "2K"
    assert s["image_url"] == ["https://r2/u/a.jpg"]
    assert "quality" not in s
    assert "details_quality" not in s
    assert "aspect_ratio" not in s  # None → пропущено


# Неизвестная/seedream модель → дефолтный профиль (только aspect_ratio).
def test_build_image_settings_default_profile() -> None:
    req = GenRequest(
        scope=SCOPE_IMAGE,
        ai_name="seedream",
        model_type="seedream-5",
        prompt="x",
        aspect_ratio="1:1",
    )
    s = SyntxClient._build_image_settings(req, [])
    assert s == {"model_type": "seedream-5", "aspect_ratio": "1:1"}


# mask_url прокидывается в settings (inpaint).
def test_build_image_settings_mask() -> None:
    req = GenRequest(scope=SCOPE_IMAGE, ai_name="banana", model_type="banana3", prompt="x")
    s = SyntxClient._build_image_settings(req, ["https://r2/u/a.jpg"], "https://r2/u/mask.png")
    assert s["mask_url"] == "https://r2/u/mask.png"
