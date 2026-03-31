# -*- coding: utf-8 -*-
"""Async-клиент для Browser Vision anti-detect API.

Документация: https://docs.browser.vision/ru/about-api

Основные методы:
- list_profiles() — список запущенных профилей
- start_profile(folder_id, profile_id) — запуск профиля → возвращает CDP-порт
- stop_profile(folder_id, profile_id) — остановка профиля
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VisionProfile:
    """Запущенный профиль Vision."""

    folder_id: str
    profile_id: str
    port: int | None


class VisionClient:
    """Async HTTP-клиент для Vision anti-detect браузера."""

    def __init__(
        self,
        x_token: str,
        base_url: str = "http://127.0.0.1:3030",
    ) -> None:
        if not x_token.strip():
            raise RuntimeError("Не задан Vision X-Token")
        self._base = base_url.rstrip("/")
        self._headers = {"X-Token": x_token.strip()}
        self._http = httpx.AsyncClient(timeout=60.0)

    async def list_profiles(self) -> list[VisionProfile]:
        """Получить список запущенных профилей."""
        resp = await self._http.get(
            f"{self._base}/list",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Vision: ответ /list: %s", data)
        profiles = data.get("profiles") or []
        return [
            VisionProfile(
                folder_id=p["folder_id"],
                profile_id=p["profile_id"],
                port=p.get("port"),
            )
            for p in profiles
        ]

    async def get_profile(self, profile_id: str) -> VisionProfile | None:
        """Возвращает профиль из /list по profile_id."""
        profiles = await self.list_profiles()
        for profile in profiles:
            if profile.profile_id == profile_id:
                return profile
        return None

    async def wait_until_profile_stopped(
        self,
        profile_id: str,
        *,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 1.0,
    ) -> bool:
        """Ждёт, пока профиль исчезнет из списка запущенных."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            profile = await self.get_profile(profile_id)
            if profile is None:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(poll_interval_seconds)

    async def wait_until_profile_has_port(
        self,
        profile_id: str,
        *,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 1.0,
    ) -> int | None:
        """Ждёт, пока у запущенного профиля появится CDP-порт."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            profile = await self.get_profile(profile_id)
            if profile is not None and profile.port is not None:
                return profile.port
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(poll_interval_seconds)

    async def resolve_folder_id(self, profile_id: str) -> str:
        """Автоматически находит folder_id для profile_id через /list."""
        profiles = await self.list_profiles()
        for p in profiles:
            if p.profile_id == profile_id:
                logger.info(
                    "Vision: folder_id=%s найден для профиля %s",
                    p.folder_id,
                    profile_id,
                )
                return p.folder_id
        raise RuntimeError(
            f"Профиль {profile_id} не найден среди запущенных. "
            f"Запустите его вручную в Vision перед стартом бота."
        )

    async def start_profile(
        self,
        folder_id: str,
        profile_id: str,
        *,
        extra_args: list[str] | None = None,
    ) -> VisionProfile:
        """Запустить профиль и получить CDP-порт для подключения.

        Args:
            folder_id: ID папки в Vision
            profile_id: ID профиля в Vision
            extra_args: дополнительные аргументы браузера

        Returns:
            VisionProfile с заполненным port для CDP-подключения
        """
        url = f"{self._base}/start/{folder_id}/{profile_id}"

        if extra_args:
            # POST с аргументами
            resp = await self._http.post(
                url,
                headers={**self._headers, "Content-Type": "application/json"},
                json={"args": extra_args},
            )
        else:
            # Простой GET-запуск
            resp = await self._http.get(url, headers=self._headers)

        resp.raise_for_status()
        data = resp.json()
        logger.info("Vision: ответ start_profile: %s", data)

        port = data.get("port")

        # Если Vision не вернул порт (профиль уже запущен) — берём из /list
        if port is None:
            logger.info("Vision: порт не в ответе start, пробуем /list")
            port = await self.wait_until_profile_has_port(profile_id)

        profile = VisionProfile(
            folder_id=data.get("folder_id", folder_id),
            profile_id=data.get("profile_id", profile_id),
            port=port,
        )
        logger.info(
            "Vision: профиль %s запущен на порту %s",
            profile.profile_id,
            profile.port,
        )
        return profile

    async def stop_profile(self, folder_id: str, profile_id: str) -> None:
        """Остановить профиль."""
        url = f"{self._base}/stop/{folder_id}/{profile_id}"
        resp = await self._http.get(url, headers=self._headers)
        resp.raise_for_status()
        logger.info("Vision: профиль %s остановлен", profile_id)

    def cdp_url(self, port: int) -> str:
        """Формирует CDP URL для Playwright connectOverCDP."""
        return f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        """Закрытие HTTP-клиента."""
        await self._http.aclose()
