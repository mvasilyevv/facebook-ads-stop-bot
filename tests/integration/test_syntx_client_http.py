# -*- coding: utf-8 -*-
"""Интеграционный: SyntxClient → реальный HTTP через httpx + respx (без живого syntx).

Покрывает полный цикл image-генерации (upload → chat → generate → poll → messages →
download), расчёт расхода токенов по дельте баланса и классификацию ошибок.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from core.syntx import SyntxClient
from core.syntx.errors import SyntxAuthError, SyntxGenerationTimeout, SyntxModerationError

_BASE = "https://api.syntx.test"
_V1 = f"{_BASE}/api/v1"
_UUID = "u-1"


def _client() -> SyntxClient:
    return SyntxClient(
        token="ey.fake.tok",
        base_url=_BASE,
        timeout_seconds=2.0,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=2.0,
    )


# Полный happy-path: 5 шагов API + дельта баланса = расход токенов, файл скачан.
@respx.mock
@pytest.mark.asyncio
async def test_generate_image_full_flow(tmp_path: Path) -> None:
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"\xff\xd8\xff\xe0jpegbytes")

    respx.get(f"{_V1}/user/balance").mock(
        side_effect=[
            Response(200, json={"balance": 100.0, "user_id": "1"}),
            Response(200, json={"balance": 72.0, "user_id": "1"}),
        ]
    )
    respx.post(f"{_V1}/chats/upload-files").mock(
        return_value=Response(
            200,
            json={
                "files": [
                    {
                        "filename": "ref.jpg",
                        "url": f"{_BASE}/r2/uploaded/ref.jpg",
                        "status": "uploaded",
                    }
                ]
            },
        )
    )
    respx.post(f"{_V1}/chats").mock(return_value=Response(201, json={"uuid": _UUID}))
    respx.post(f"{_V1}/design/generate").mock(return_value=Response(200, json={"id": 111}))
    respx.get(f"{_V1}/chats/{_UUID}/inprogress").mock(
        side_effect=[
            Response(200, json=[{"message_id": 1, "object_type": "image", "task_id": None}]),
            Response(200, json=[]),
        ]
    )
    respx.get(f"{_V1}/chats/{_UUID}/messages").mock(
        return_value=Response(
            200,
            json={
                "messages": [
                    {
                        "message_object": [
                            {"object_type": "image", "object_url": f"{_BASE}/r2/generated/out.jpg"},
                        ]
                    }
                ]
            },
        )
    )
    respx.get(f"{_BASE}/r2/generated/out.jpg").mock(return_value=Response(200, content=b"PNGDATA"))

    out = tmp_path / "result.jpg"
    from core.syntx import GenRequest

    async with _client() as cl:
        res = await cl.generate_image(
            GenRequest(
                scope="image",
                ai_name="sora-images",
                model_type="gpt-image-2",
                prompt="banner",
                image_refs=(str(ref),),
            ),
            download_to=out,
        )

    assert res.chat_uuid == _UUID
    assert res.message_id == 111
    assert res.image_urls == (f"{_BASE}/r2/generated/out.jpg",)
    assert res.local_paths == (out,)
    assert out.read_bytes() == b"PNGDATA"
    # расход = баланс_до - баланс_после = 100 - 72
    assert res.tokens_spent == pytest.approx(28.0)


# Нет /generated/ + маркер модерации в messages → SyntxModerationError (гемблинг-кейс).
@respx.mock
@pytest.mark.asyncio
async def test_generate_image_moderation() -> None:
    respx.get(f"{_V1}/user/balance").mock(return_value=Response(200, json={"balance": 50.0}))
    respx.post(f"{_V1}/chats").mock(return_value=Response(201, json={"uuid": _UUID}))
    respx.post(f"{_V1}/design/generate").mock(return_value=Response(200, json={"id": 1}))
    respx.get(f"{_V1}/chats/{_UUID}/inprogress").mock(return_value=Response(200, json=[]))
    respx.get(f"{_V1}/chats/{_UUID}/messages").mock(
        return_value=Response(
            200,
            json={
                "messages": [
                    {
                        "message_object": [
                            {"object_type": "text", "object_text": "image_violation: blocked"},
                        ]
                    }
                ]
            },
        )
    )
    from core.syntx import GenRequest

    async with _client() as cl:
        with pytest.raises(SyntxModerationError):
            await cl.generate_image(
                GenRequest(
                    scope="image", ai_name="sora-images", model_type="gpt-image-2", prompt="x"
                )
            )


# /inprogress никогда не пустеет → SyntxGenerationTimeout (ветка таймаута поллинга).
@respx.mock
@pytest.mark.asyncio
async def test_generate_image_timeout() -> None:
    respx.get(f"{_V1}/user/balance").mock(return_value=Response(200, json={"balance": 50.0}))
    respx.post(f"{_V1}/chats").mock(return_value=Response(201, json={"uuid": _UUID}))
    respx.post(f"{_V1}/design/generate").mock(return_value=Response(200, json={"id": 1}))
    respx.get(f"{_V1}/chats/{_UUID}/inprogress").mock(
        return_value=Response(200, json=[{"message_id": 1, "object_type": "image"}])
    )
    from core.syntx import GenRequest

    cl = SyntxClient(
        token="ey.fake.tok",
        base_url=_BASE,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.05,
    )
    async with cl:
        with pytest.raises(SyntxGenerationTimeout):
            await cl.generate_image(
                GenRequest(
                    scope="image", ai_name="sora-images", model_type="gpt-image-2", prompt="x"
                )
            )


# edit_image: роутится в banana (faithful), исходник идёт через settings.image_url,
# нет quality/details_quality (иначе чёрный кадр).
@respx.mock
@pytest.mark.asyncio
async def test_edit_image_routes_to_banana(tmp_path: Path) -> None:
    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0jpg")

    respx.get(f"{_V1}/user/balance").mock(return_value=Response(200, json={"balance": 50.0}))
    respx.post(f"{_V1}/chats/upload-files").mock(
        return_value=Response(
            200, json={"files": [{"filename": "src.jpg", "url": f"{_BASE}/r2/uploaded/src.jpg"}]}
        )
    )
    respx.post(f"{_V1}/chats").mock(return_value=Response(201, json={"uuid": _UUID}))
    gen_route = respx.post(f"{_V1}/design/generate").mock(
        return_value=Response(200, json={"id": 9})
    )
    respx.get(f"{_V1}/chats/{_UUID}/inprogress").mock(return_value=Response(200, json=[]))
    respx.get(f"{_V1}/chats/{_UUID}/messages").mock(
        return_value=Response(
            200,
            json={
                "messages": [
                    {
                        "message_object": [
                            {"object_type": "image", "object_url": f"{_BASE}/r2/generated/e.png"}
                        ]
                    }
                ]
            },
        )
    )

    async with _client() as cl:
        res = await cl.edit_image(str(src), "replace GHANA with KENYA")

    assert res.image_urls == (f"{_BASE}/r2/generated/e.png",)
    # генерация ушла в banana и с исходником в settings.image_url
    req = gen_route.calls.last.request
    assert "ai_name=banana" in str(req.url)
    import json as _json

    body = _json.loads(req.content)
    assert body["settings"]["model_type"] == "banana3"
    assert body["settings"]["image_url"] == [f"{_BASE}/r2/uploaded/src.jpg"]
    assert "quality" not in body["settings"]
    assert "details_quality" not in body["settings"]


# 401 на каталоге → SyntxAuthError (токен протух).
@respx.mock
@pytest.mark.asyncio
async def test_list_models_auth_error() -> None:
    respx.get(f"{_V1}/ai").mock(return_value=Response(401, json={"detail": "unauthorized"}))
    respx.get(f"{_V1}/ai/models").mock(return_value=Response(401, json={"detail": "unauthorized"}))
    async with _client() as cl:
        with pytest.raises(SyntxAuthError):
            await cl.list_models()
