"""Безопасная read-only проверка токена и профиля Vision Cloud."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

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
    http_client: httpx.AsyncClient | None = None,
) -> VisionCloudProbe:
    """Проверить X-Token через облако и найти профиль в его списке.

    Ошибки сети, неожиданные статусы и некорректный JSON намеренно сводятся к
    ``unavailable``. Текст ответа не читается и не попадает в логи.
    """

    client = http_client or httpx.AsyncClient(timeout=5.0)
    owns_client = http_client is None
    headers = {"X-Token": token}
    base_url = vision_cloud_url.rstrip("/")
    try:
        try:
            me_response = await client.get(f"{base_url}/users/me", headers=headers)
        except httpx.HTTPError:
            return VisionCloudProbe("unavailable")
        if _is_auth_rejection(me_response.status_code):
            return VisionCloudProbe("token_rejected")
        if not me_response.is_success:
            return VisionCloudProbe("unavailable")

        try:
            profiles_response = await client.get(f"{base_url}/list", headers=headers)
        except httpx.HTTPError:
            return VisionCloudProbe("unavailable")
        if _is_auth_rejection(profiles_response.status_code):
            return VisionCloudProbe("token_rejected")
        if not profiles_response.is_success:
            return VisionCloudProbe("unavailable")

        try:
            payload = profiles_response.json()
        except ValueError:
            return VisionCloudProbe("unavailable")
        profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if not isinstance(profiles, list):
            return VisionCloudProbe("unavailable")
        profile_found = any(
            isinstance(profile, dict)
            and str(profile.get("profile_id") or "").strip() == profile_id.strip()
            for profile in profiles
        )
        return VisionCloudProbe("ready" if profile_found else "profile_not_found")
    finally:
        if owns_client:
            await client.aclose()


__all__ = ["VisionCloudProbe", "VisionCloudProbeState", "probe_vision_cloud"]
