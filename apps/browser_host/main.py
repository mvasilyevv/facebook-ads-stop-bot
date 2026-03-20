from __future__ import annotations

import asyncio
import logging

from apps.browser_host.adapters.base import AntiDetectAdapter
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from core.logging import configure_logging


class UnsupportedAdapter(AntiDetectAdapter):
    async def list_profiles(self) -> list:
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def list_open_profiles(self) -> list:
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def get_profile_status(self, profile_id: str):
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def stop_profile(self, profile_id: str) -> None:
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def start_profile_for_automation(
        self,
        profile_id: str,
        launch_mode: str,
        launch_args: list[str] | None = None,
    ):
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def ensure_single_active_profile(self) -> None:
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")

    async def healthcheck(self):
        raise RuntimeError("Для browser host пока не выбран конкретный anti-detect адаптер")


async def run_browser_host() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    manager = BrowserSessionManager(
        adapter=UnsupportedAdapter(),
        playwright_attach_service=PlaywrightAttachService(),
    )
    logger.info("Browser host запущен и ожидает конфигурацию адаптера")
    await manager.healthcheck()


def main() -> None:
    asyncio.run(run_browser_host())


if __name__ == "__main__":
    main()
