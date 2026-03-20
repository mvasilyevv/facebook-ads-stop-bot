from __future__ import annotations

from apps.browser_host.adapters.base import AntiDetectAdapter
from apps.browser_host.adapters.vision import VisionAdapter, VisionAdapterSettings
from core.config import Settings


def build_adapter(settings: Settings) -> AntiDetectAdapter:
    """Создает anti-detect адаптер по выбранному вендору."""

    vendor = settings.browser_vendor.strip().lower()
    if vendor == "vision":
        return VisionAdapter(
            settings=VisionAdapterSettings(
                api_token=settings.vision_api_token,
                cloud_api_url=settings.vision_cloud_api_url,
                local_api_url=settings.vision_local_api_url,
                timeout_seconds=settings.vision_timeout_seconds,
            )
        )
    raise RuntimeError(f"Неизвестный anti-detect вендор: {settings.browser_vendor}")
