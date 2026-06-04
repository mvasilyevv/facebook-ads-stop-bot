# -*- coding: utf-8 -*-
"""Поднять активную Vision-сессию в browser-agent (StartBrowser) — точечно,
без запуска всего стека. Нужно когда browser-agent поднят, но сессии нет
(«Активная browser-agent сессия не найдена»).

    python scripts/start_vision_session.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.config import get_settings


async def main() -> int:
    s = get_settings()
    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=s.vision_x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )
    await client.start()
    try:
        print(f"Vision profile: {s.vision_profile_id or 'default'} · подключаюсь…")
        sid = await client.start_browser()
        print(f"✅ session_id = {sid}")
        print(f"   cdp_url = {client.cdp_url}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
