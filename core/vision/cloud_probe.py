"""Безопасная read-only проверка токена и профиля Vision Cloud."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from core.vision.cloud_profiles import list_vision_profiles

VisionCloudProbeState = Literal[
    "ready",
    "token_rejected",
    "profile_not_found",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class VisionCloudProbe:
    """Ограниченный результат проверки без токена, тела ответа или исключения."""

    state: VisionCloudProbeState


def _is_auth_rejection(status_code: int) -> bool:
    return status_code in {401, 403}


async def probe_vision_cloud(
    vision_cloud_url: str,
    *,
    token: str,
    profile_id: str,
    folder_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> VisionCloudProbe:
    """Проверить X-Token через облако и найти профиль в списке его папки.

    Ошибки сети, неожиданные статусы и некорректный JSON намеренно сводятся к
    ``unavailable``. Текст ответа не читается и не попадает в логи.
    """

    client = http_client or httpx.AsyncClient(timeout=5.0)
    owns_client = http_client is None
    base_url = vision_cloud_url.rstrip("/")
    try:
        try:
            me_response = await client.get(f"{base_url}/users/me", headers={"X-Token": token})
        except httpx.HTTPError:
            return VisionCloudProbe("unavailable")
        if _is_auth_rejection(me_response.status_code):
            return VisionCloudProbe("token_rejected")
        if not me_response.is_success:
            return VisionCloudProbe("unavailable")

        # Профили перечисляются только внутри папки. Без неё список недостижим,
        # и объявлять профиль отсутствующим нельзя — мы его просто не искали.
        if not folder_id.strip():
            return VisionCloudProbe("unavailable")

        profiles = await list_vision_profiles(
            vision_cloud_url,
            token=token,
            folder_id=folder_id,
            http_client=client,
        )
        if profiles.state == "token_rejected":
            return VisionCloudProbe("token_rejected")
        if profiles.state != "ready":
            return VisionCloudProbe("unavailable")

        expected = profile_id.strip()
        found = any(profile.id == expected for profile in profiles.items)
        return VisionCloudProbe("ready" if found else "profile_not_found")
    finally:
        if owns_client:
            await client.aclose()


__all__ = ["VisionCloudProbe", "VisionCloudProbeState", "probe_vision_cloud"]
