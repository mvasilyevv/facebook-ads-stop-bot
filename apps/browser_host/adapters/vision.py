from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from apps.browser_host.adapters.errors import AdapterConnectionError, AdapterProtocolError
from apps.browser_host.adapters.models import (
    AdapterHealth,
    AutomationLaunchResult,
    OpenProfileInfo,
    ProfileInfo,
    ProfileStatus,
)


@dataclass(slots=True, frozen=True)
class VisionAdapterSettings:
    """Настройки для работы с облачным и локальным API Vision."""

    api_token: str
    cloud_api_url: str = "https://v1.empr.cloud/api/v1"
    local_api_url: str = "http://127.0.0.1:3030"
    timeout_seconds: float = 10.0


# Кеш folder_id → profile_id, чтобы не зависеть от облачного API
_folder_id_cache: dict[str, str] = {}


class VisionAdapter:
    """Адаптер для anti-detect браузера Vision."""

    def __init__(
        self,
        settings: VisionAdapterSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def list_profiles(self) -> list[ProfileInfo]:
        open_profiles = await self.list_open_profiles()
        open_profile_ids = {profile.profile_id for profile in open_profiles}
        result: list[ProfileInfo] = []
        for folder in await self._list_folders():
            folder_id = self._extract_required_value(
                folder,
                ("folder_id", "id"),
                context="список папок Vision",
            )
            for profile in await self._list_profiles_in_folder(folder_id):
                profile_id = self._extract_required_value(
                    profile,
                    ("profile_id", "id"),
                    context=f"список профилей папки {folder_id}",
                )
                display_name = (
                    self._extract_optional_value(
                        profile,
                        ("profile_name", "name", "title"),
                    )
                    or profile_id
                )
                result.append(
                    ProfileInfo(
                        profile_id=profile_id,
                        display_name=display_name,
                        is_active=profile_id in open_profile_ids,
                    )
                )
        return result

    async def list_open_profiles(self) -> list[OpenProfileInfo]:
        payload = await self._request_json(
            method="GET",
            url=f"{self._settings.local_api_url.rstrip('/')}/list",
            context="список открытых профилей Vision",
        )
        raw_list = payload.get("data") or payload.get("profiles")
        if not isinstance(raw_list, list):
            raise AdapterProtocolError(
                "Vision не вернул список данных для операции: список открытых профилей Vision"
            )
        open_profiles = [item for item in raw_list if isinstance(item, dict)]
        for item in open_profiles:
            pid = item.get("profile_id") or item.get("id")
            fid = item.get("folder_id")
            if pid and fid:
                _folder_id_cache[str(pid)] = str(fid)
        result: list[OpenProfileInfo] = []
        for item in open_profiles:
            profile_id = self._extract_required_value(
                item,
                ("profile_id", "id"),
                context="список открытых профилей Vision",
            )
            display_name = (
                self._extract_optional_value(
                    item,
                    ("name", "profile_name", "title"),
                )
                or profile_id
            )
            port = item.get("port")
            debug_endpoint = None
            if port:
                debug_endpoint = f"http://127.0.0.1:{int(port)}"
            result.append(
                OpenProfileInfo(
                    profile_id=profile_id,
                    display_name=display_name,
                    debug_endpoint=debug_endpoint,
                )
            )
        return result

    async def get_profile_status(self, profile_id: str) -> ProfileStatus:
        open_profiles = {item.profile_id: item for item in await self.list_open_profiles()}
        if profile_id in open_profiles:
            profile = open_profiles[profile_id]
            return ProfileStatus(
                profile_id=profile_id,
                state="RUNNING",
                has_automation_binding=profile.debug_endpoint is not None,
            )

        return ProfileStatus(
            profile_id=profile_id,
            state="STOPPED",
            has_automation_binding=False,
        )

    async def stop_profile(self, profile_id: str) -> None:
        folder_id = await self._resolve_folder_id(profile_id)
        url = f"{self._settings.local_api_url.rstrip('/')}/stop/{folder_id}/{profile_id}"
        async with httpx.AsyncClient(
            timeout=self._settings.timeout_seconds,
            transport=self._transport,
        ) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AdapterConnectionError(
                    f"Vision API вернул статус {exc.response.status_code} "
                    f"для операции: остановка профиля {profile_id}"
                ) from exc
            except httpx.HTTPError as exc:
                raise AdapterConnectionError(
                    f"Не удалось обратиться к Vision API для операции: "
                    f"остановка профиля {profile_id}"
                ) from exc

    async def start_profile_for_automation(
        self,
        profile_id: str,
        launch_mode: str,
        launch_args: list[str] | None = None,
    ) -> AutomationLaunchResult:
        if launch_mode.strip().lower() != "cdp":
            raise RuntimeError("Vision сейчас поддерживается только в режиме CDP")

        debug_port = random.randint(10000, 59999)
        args = list(launch_args or [])
        if not any("--remote-debugging-port" in a for a in args):
            args.append(f"--remote-debugging-port={debug_port}")

        folder_id = await self._resolve_folder_id(profile_id)
        payload = await self._request_json(
            method="POST",
            url=f"{self._settings.local_api_url.rstrip('/')}/start/{folder_id}/{profile_id}",
            context=f"запуск профиля {profile_id}",
            json={"args": args},
        )
        port = payload.get("port") or debug_port
        browser_pid = payload.get("pid")
        cdp_url = f"http://127.0.0.1:{int(port)}"
        return AutomationLaunchResult(
            profile_id=profile_id,
            vendor="vision",
            cdp_url=cdp_url,
            webdriver_url=None,
            debug_port=int(port),
            browser_pid=int(browser_pid) if browser_pid is not None else None,
            launched_at=datetime.now(tz=UTC),
        )

    async def ensure_single_active_profile(self) -> None:
        import logging as _logging

        open_profiles = await self.list_open_profiles()
        if len(open_profiles) > 1:
            _logging.getLogger(__name__).warning(
                "Vision: несколько активных профилей (%s). "
                "Для automation будет использован указанный profile_id.",
                len(open_profiles),
            )

    async def healthcheck(self) -> AdapterHealth:
        if not self._settings.api_token:
            return AdapterHealth(
                is_healthy=False,
                message="Не задан токен Vision API в переменной VISION_API_TOKEN",
            )

        try:
            open_profiles = await self.list_open_profiles()
        except (AdapterConnectionError, AdapterProtocolError) as exc:
            return AdapterHealth(is_healthy=False, message=str(exc))

        cloud_ok = True
        folder_count = 0
        try:
            folders = await self._list_folders()
            folder_count = len(folders)
        except (AdapterConnectionError, AdapterProtocolError):
            cloud_ok = False

        msg = f"Vision Local API доступен. Открытых профилей: {len(open_profiles)}"
        if cloud_ok:
            msg += f". Cloud API доступен, папок: {folder_count}"
        else:
            msg += ". Cloud API недоступен (не критично для автоматизации)"
        return AdapterHealth(is_healthy=True, message=msg)

    async def _resolve_folder_id(self, profile_id: str) -> str:
        """Определяет folder_id: кеш → локальный /list → облачный API."""
        if profile_id in _folder_id_cache:
            return _folder_id_cache[profile_id]

        try:
            payload = await self._request_json(
                method="GET",
                url=f"{self._settings.local_api_url.rstrip('/')}/list",
                context="поиск folder_id открытого профиля",
            )
            raw_list = payload.get("data") or payload.get("profiles") or []
            for item in raw_list:
                if isinstance(item, dict):
                    pid = item.get("profile_id")
                    fid = item.get("folder_id")
                    if pid and fid:
                        _folder_id_cache[str(pid)] = str(fid)
            if profile_id in _folder_id_cache:
                return _folder_id_cache[profile_id]
        except (AdapterConnectionError, AdapterProtocolError):
            pass

        profile = await self._resolve_profile(profile_id)
        fid = self._extract_required_value(profile, ("folder_id",), context=f"профиль {profile_id}")
        _folder_id_cache[profile_id] = fid
        return fid

    async def _resolve_profile(self, profile_id: str) -> dict[str, Any]:
        for folder in await self._list_folders():
            folder_id = self._extract_required_value(
                folder,
                ("folder_id", "id"),
                context="список папок Vision",
            )
            for profile in await self._list_profiles_in_folder(folder_id):
                current_profile_id = self._extract_required_value(
                    profile,
                    ("profile_id", "id"),
                    context=f"список профилей папки {folder_id}",
                )
                if current_profile_id == profile_id:
                    profile["folder_id"] = folder_id
                    return profile
        raise RuntimeError(f"Профиль Vision с идентификатором {profile_id} не найден")

    async def _list_folders(self) -> list[dict[str, Any]]:
        payload = await self._request_json(
            method="GET",
            url=f"{self._settings.cloud_api_url.rstrip('/')}/folders",
            context="список папок Vision",
        )
        return self._extract_data_list(payload, "список папок Vision")

    async def _list_profiles_in_folder(self, folder_id: str) -> list[dict[str, Any]]:
        page_number = 1
        page_size = 100
        result: list[dict[str, Any]] = []
        while True:
            payload = await self._request_json(
                method="GET",
                url=f"{self._settings.cloud_api_url.rstrip('/')}/folders/{folder_id}/profiles",
                context=f"список профилей папки {folder_id}",
                params={"pn": page_number, "ps": page_size},
            )
            page = self._extract_data_dict(payload, f"список профилей папки {folder_id}")
            items = page.get("items")
            if not isinstance(items, list):
                raise AdapterProtocolError(
                    f"Vision вернул неожиданный формат профилей для папки {folder_id}"
                )
            result.extend(item for item in items if isinstance(item, dict))
            if len(items) < page_size:
                break
            page_number += 1
        return result

    async def _request_json(
        self,
        method: str,
        url: str,
        context: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = {"X-Token": self._settings.api_token}
        request_headers = kwargs.pop("headers", {})
        headers.update(request_headers)
        async with httpx.AsyncClient(
            timeout=self._settings.timeout_seconds,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AdapterConnectionError(
                    f"Vision API вернул статус {exc.response.status_code} для операции: {context}"
                ) from exc
            except httpx.HTTPError as exc:
                raise AdapterConnectionError(
                    f"Не удалось обратиться к Vision API для операции: {context}"
                ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterProtocolError(
                f"Vision API вернул невалидный JSON для операции: {context}"
            ) from exc
        if not isinstance(payload, dict):
            raise AdapterProtocolError(
                f"Vision API вернул неожиданный тип ответа для операции: {context}"
            )
        return payload

    @staticmethod
    def _extract_data_list(payload: dict[str, Any], context: str) -> list[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise AdapterProtocolError(f"Vision не вернул список данных для операции: {context}")
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _extract_data_dict(payload: dict[str, Any], context: str) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AdapterProtocolError(f"Vision не вернул объект данных для операции: {context}")
        return data

    @staticmethod
    def _extract_required_value(
        payload: dict[str, Any],
        keys: tuple[str, ...],
        context: str,
    ) -> str:
        value = VisionAdapter._extract_optional_value(payload, keys)
        if not value:
            raise AdapterProtocolError(
                f"Vision не вернул обязательное поле {keys[0]} для операции: {context}"
            )
        return value

    @staticmethod
    def _extract_optional_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None
