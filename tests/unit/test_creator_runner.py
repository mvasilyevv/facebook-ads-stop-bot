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


# Проверяем что зарегистрированный binding реально форвардит событие в on_emit.
def test_runner_binding_forwards_to_on_emit():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()

    emitted = []
    runner = CreatorRunner(page, bundle_code="window.__fbAgent={};")
    asyncio.run(runner.attach(on_emit=lambda ev, payload: emitted.append((ev, payload))))

    # Достаём зарегистрированный callback и вызываем его как Playwright бы вызвал.
    _, registered_cb = page.expose_binding.call_args[0]
    asyncio.run(registered_cb({"page": page}, "step_done", {"name": "create_campaign"}))

    assert emitted == [("step_done", {"name": "create_campaign"})]
