# Проверяем что CreatorRunner инжектит бандл через addInitScript и биндит fbAgentEmit.
import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.creator_bridge.runner import CreatorRunner


def test_runner_attaches_bundle_and_binding():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()

    runner = CreatorRunner(page, bundle_code="window.__fbAgent={};")
    emitted = []
    asyncio.run(runner.attach(on_emit=lambda ev, payload: emitted.append((ev, payload))))

    page.add_init_script.assert_awaited_once()
    page.expose_binding.assert_awaited_once()
    args, _ = page.expose_binding.call_args
    assert args[0] == "fbAgentEmit"
