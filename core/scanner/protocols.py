from __future__ import annotations

from typing import Protocol

from core.scanner.models import ScannedAdRow


class ScannerProvider(Protocol):
    """Контракт для провайдера сканирования объявлений."""

    async def scan_rows(self, profile_id: str, browser_host_name: str) -> list[ScannedAdRow]: ...
