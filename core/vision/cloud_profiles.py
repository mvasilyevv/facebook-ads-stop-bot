"""Живой список профилей Vision Cloud — имена вместо UUID в интерфейсе.

Список никогда не кэшируется: оператор должен видеть облако таким, какое оно
сейчас. Если профиль переименовали, пересоздали или удалили, это видно при
первом же открытии настроек, а не после перезапуска сервиса.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote

import httpx

VisionProfilesState = Literal[
    "ready",
    "token_rejected",
    "folder_not_found",
    "unavailable",
]

DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class VisionCloudProfile:
    """Профиль так, как его показывает Vision — без внутренних полей."""

    id: str
    name: str
    status: str | None = None
    tags: tuple[str, ...] = ()
    running: bool = False
    last_run_at: str | None = None


@dataclass(frozen=True, slots=True)
class VisionCloudProfiles:
    """Результат без токена, тела ответа и исключения внутри."""

    state: VisionProfilesState
    items: tuple[VisionCloudProfile, ...] = field(default=())


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status(value: Any) -> str | None:
    """Статус приходит объектом со `status`, реже — готовой строкой."""
    if isinstance(value, str):
        return _text(value) or None
    if isinstance(value, dict):
        for key in ("status", "name", "title"):
            text = _text(value.get(key))
            if text:
                return text
    return None


def _tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    tags: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = _text(item)
        elif isinstance(item, dict):
            text = next(
                (_text(item.get(key)) for key in ("tag", "name", "title") if item.get(key)), ""
            )
        else:
            text = ""
        if text:
            tags.append(text)
    return tuple(tags)


def _profile(raw: Any) -> VisionCloudProfile | None:
    if not isinstance(raw, dict):
        return None
    identifier = _text(raw.get("id"))
    if not identifier:
        return None
    return VisionCloudProfile(
        id=identifier,
        # Безымянный профиль показываем идентификатором: пустая строка в списке
        # выглядит как сбой загрузки, хотя профиль существует.
        name=_text(raw.get("profile_name")) or identifier,
        status=_status(raw.get("profile_status")),
        tags=_tags(raw.get("profile_tags")),
        running=raw.get("running") is True,
        last_run_at=_text(raw.get("last_run_at")) or None,
    )


async def list_vision_profiles(
    vision_cloud_url: str,
    *,
    token: str,
    folder_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> VisionCloudProfiles:
    """Прочитать профили папки.

    Сеть, неожиданные статусы и некорректный JSON сводятся к `unavailable`:
    пустой список означает «в папке пусто», и подменять им сбой нельзя.
    Тело ответа не читается и в результат не попадает.
    """

    client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
    owns_client = http_client is None
    base_url = vision_cloud_url.rstrip("/")
    folder_path = quote(folder_id.strip(), safe="")
    try:
        try:
            response = await client.get(
                f"{base_url}/folders/{folder_path}/profiles",
                headers={"X-Token": token},
            )
        except httpx.HTTPError:
            return VisionCloudProfiles("unavailable")

        if response.status_code in {401, 403}:
            return VisionCloudProfiles("token_rejected")
        if response.status_code == 404:
            return VisionCloudProfiles("folder_not_found")
        if not response.is_success:
            return VisionCloudProfiles("unavailable")

        try:
            payload = response.json()
        except ValueError:
            return VisionCloudProfiles("unavailable")

        data = payload.get("data") if isinstance(payload, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return VisionCloudProfiles("unavailable")

        profiles = tuple(profile for profile in map(_profile, items) if profile is not None)
        return VisionCloudProfiles("ready", profiles)
    finally:
        if owns_client:
            await client.aclose()


__all__ = [
    "VisionCloudProfile",
    "VisionCloudProfiles",
    "VisionProfilesState",
    "list_vision_profiles",
]
