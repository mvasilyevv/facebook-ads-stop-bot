"""Инжект creator-бандла на страницу + биндинг обратной связи."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CreatorRunner:
    """Связывает Playwright Page с TS-бандлом creator-агента."""

    def __init__(self, page: Any, bundle_code: str) -> None:
        self._page = page
        self._bundle_code = bundle_code

    async def attach(self, on_emit: Callable[[str, Any], None]) -> None:
        """Инжектит бандл и регистрирует binding fbAgentEmit."""
        await self._page.add_init_script(self._bundle_code)

        async def _binding(_source: dict[str, Any], event: str, payload: Any = None) -> None:
            on_emit(event, payload)

        await self._page.expose_binding("fbAgentEmit", _binding)

    async def run_plan(self, plan: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        """Вызывает window.__fbAgent.run(plan, variables) на странице."""
        return await self._page.evaluate(
            "([plan, vars]) => window.__fbAgent.run(plan, vars)",
            [plan, variables],
        )
