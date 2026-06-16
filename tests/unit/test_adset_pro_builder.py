# -*- coding: utf-8 -*-
"""Unit: AdsetProBuilder — confirm-first скаффолдинг + резолв id по имени (мок MCP)."""

from __future__ import annotations

import pytest

from core.adset_pro.builder import AdsetProBuilder, BuildPlan


class _StubClient:
    """Подменяет AdsetProClient.call_mcp_tool: list_* отдаёт фикстуру, create_* — id."""

    def __init__(self, lists: dict) -> None:
        self._lists = lists
        self.calls: list[tuple[str, dict]] = []

    async def call_mcp_tool(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        if tool.startswith("list_"):
            return self._lists.get(tool, {"items": []})
        return {"id": "new_" + tool, "ok": True}


_LISTS = {
    "list_cpas": {
        "items": [{"id": "cpa1", "name": "Offerleader"}, {"id": "cpa2", "name": "Monetro"}]
    },
    "list_offers": {"items": [{"id": "off1", "name": "GH_CR Aviator"}]},
    "list_sources": {"items": [{"id": "src1", "name": "Facebook MV"}]},
}


def _builder(stub: _StubClient) -> AdsetProBuilder:
    return AdsetProBuilder(stub)  # type: ignore[arg-type]


# confirm=False → BuildPlan, реального create НЕ происходит (только list для резолва).
@pytest.mark.asyncio
async def test_create_offer_plan_no_write() -> None:
    stub = _StubClient(_LISTS)
    res = await _builder(stub).create_offer(name="GH_CR", cpa="Offerleader", revenue=12.0)
    assert isinstance(res, BuildPlan)
    assert res.tool == "create_offer"
    assert res.args["cpaId"] == "cpa1"  # резолв по имени
    assert res.args["revenue"] == 12.0
    assert all(t == "list_cpas" for t, _ in stub.calls)  # никаких create_*


# confirm=True → реально зовёт create_offer с резолвленными args.
@pytest.mark.asyncio
async def test_create_offer_confirm_writes() -> None:
    stub = _StubClient(_LISTS)
    res = await _builder(stub).create_offer(name="GH_CR", cpa="Offerleader", confirm=True)
    assert isinstance(res, dict) and res["id"] == "new_create_offer"
    created = [c for c in stub.calls if c[0] == "create_offer"]
    assert created and created[0][1]["cpaId"] == "cpa1" and created[0][1]["name"] == "GH_CR"


# 24-hex id передаётся как есть, без обращения к list_*.
@pytest.mark.asyncio
async def test_resolve_id_passthrough() -> None:
    stub = _StubClient(_LISTS)
    plan = await _builder(stub).create_offer(name="x", cpa="6a22bd5ca30edee8e2d45c04")
    assert plan.args["cpaId"] == "6a22bd5ca30edee8e2d45c04"
    assert stub.calls == []  # резолв не понадобился


# Имя не найдено → ValueError.
@pytest.mark.asyncio
async def test_resolve_not_found() -> None:
    stub = _StubClient(_LISTS)
    with pytest.raises(ValueError):
        await _builder(stub).create_offer(name="x", cpa="НетТакого")


# create_campaign без source/domain → args только {name} (None отфильтрованы).
@pytest.mark.asyncio
async def test_create_campaign_minimal_plan() -> None:
    stub = _StubClient(_LISTS)
    plan = await _builder(stub).create_campaign(name="CR2 | GH | MV | 17.06")
    assert isinstance(plan, BuildPlan)
    assert plan.args == {"name": "CR2 | GH | MV | 17.06"}


# create_flow резолвит и cpa, и offer.
@pytest.mark.asyncio
async def test_create_flow_resolves_both() -> None:
    stub = _StubClient(_LISTS)
    plan = await _builder(stub).create_flow(
        name="f1", cpa="Offerleader", offer="GH_CR Aviator", url="https://t.example/c"
    )
    assert plan.args["cpaId"] == "cpa1"
    assert plan.args["offerId"] == "off1"
    assert plan.args["url"] == "https://t.example/c"
