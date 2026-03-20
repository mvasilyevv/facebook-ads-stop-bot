from __future__ import annotations

from typing import Protocol

from apps.browser_host.adapters.models import (
    AdapterHealth,
    AutomationLaunchResult,
    OpenProfileInfo,
    ProfileInfo,
    ProfileStatus,
)


class AntiDetectAdapter(Protocol):
    """Контракт для интеграции с локальным API anti-detect браузера."""

    async def list_profiles(self) -> list[ProfileInfo]: ...

    async def list_open_profiles(self) -> list[OpenProfileInfo]: ...

    async def get_profile_status(self, profile_id: str) -> ProfileStatus: ...

    async def stop_profile(self, profile_id: str) -> None: ...

    async def start_profile_for_automation(
        self,
        profile_id: str,
        launch_mode: str,
        launch_args: list[str] | None = None,
    ) -> AutomationLaunchResult: ...

    async def ensure_single_active_profile(self) -> None: ...

    async def healthcheck(self) -> AdapterHealth: ...
