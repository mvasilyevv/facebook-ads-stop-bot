# -*- coding: utf-8 -*-
"""Unit: get_tracker_stats (AdSet.pro post-click статистика).

Клиент AdSet.pro замокан — сети нет, проверяем парсинг/формат/валидацию.
"""

from __future__ import annotations

import pytest

from core.adset_pro.errors import AdsetProError
from core.ai_assistant.tools.base import ToolContext, ToolError
from core.ai_assistant.tools.trackers import get_tracker_stats as mod
from core.ai_assistant.tools.trackers.get_tracker_stats import GetTrackerStatsTool


class _FakeClient:
    """Async-context заглушка AdsetProClient: ловит args, отдаёт payload/исключение."""

    def __init__(self, payload=None, raise_exc=None):
        self.payload = payload if payload is not None else {"data": []}
        self.raise_exc = raise_exc
        self.captured = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_mcp_tool(self, name, args):
        self.captured = (name, args)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.payload


def _patch_client(monkeypatch, fake):
    async def _factory(engine, **kw):
        return fake

    monkeypatch.setattr(mod, "create_adsetpro_client", _factory)


def _ctx():
    return ToolContext(client_key="t", engine=object())


# Тоталы без разреза: метрики в строке, депозиты = ftds
@pytest.mark.asyncio
async def test_totals_no_group(monkeypatch):
    fake = _FakeClient({"data": [{"clicks": 100, "registrations": 5, "ftds": 2, "revenue": 30}]})
    _patch_client(monkeypatch, fake)
    out = await GetTrackerStatsTool().run(_ctx(), {"days": 7})
    assert "итого" in out
    assert "Клики: 100" in out and "Реги: 5" in out
    assert "Депозиты (FTD): 2" in out and "$30.00" in out
    assert fake.captured[0] == "query_stats"
    assert "groups" not in fake.captured[1]  # без group_by не шлём groups


# Разрез по event_type: сортировка по FTD desc + ИТОГО суммирует ВСЕ строки (не только показанные)
@pytest.mark.asyncio
async def test_group_event_type_sorted_and_totals(monkeypatch):
    payload = {
        "data": [
            {"event_type": "CPA_HOLD", "clicks": 0, "registrations": 50, "ftds": 0, "revenue": 0},
            {
                "event_type": "CPA_ACCEPT",
                "clicks": 0,
                "registrations": 0,
                "ftds": 11,
                "revenue": 59,
            },
            {
                "event_type": "SOURCE_CLICK",
                "clicks": 1334,
                "registrations": 0,
                "ftds": 0,
                "revenue": 0,
            },
        ]
    }
    _patch_client(monkeypatch, _FakeClient(payload))
    out = await GetTrackerStatsTool().run(
        _ctx(), {"days": 90, "group_by": "event_type", "limit": 1}
    )
    lines = out.splitlines()
    # первый показанный — с максимальным FTD
    assert "CPA_ACCEPT" in lines[1]
    assert "ещё 2 строк" in out  # limit=1 из 3
    # ИТОГО по всем 3 строкам: реги 50, FTD 11
    assert "ИТОГО (3)" in out and "реги 50" in out and "FTD 11" in out


# Невалидный дименшен → ToolError со списком разрешённых
@pytest.mark.asyncio
async def test_invalid_group_by(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(ToolError, match="group_by"):
        await GetTrackerStatsTool().run(_ctx(), {"group_by": "country"})


# Пустой ответ трекера → понятное сообщение, не падение
@pytest.mark.asyncio
async def test_empty_data(monkeypatch):
    _patch_client(monkeypatch, _FakeClient({"data": []}))
    out = await GetTrackerStatsTool().run(_ctx(), {"days": 3})
    assert "нет данных" in out


# Ошибка AdSet.pro → ToolError (не утечка исключения)
@pytest.mark.asyncio
async def test_adsetpro_error_wrapped(monkeypatch):
    _patch_client(monkeypatch, _FakeClient(raise_exc=AdsetProError("503 boom")))
    with pytest.raises(ToolError, match="AdSet.pro недоступен"):
        await GetTrackerStatsTool().run(_ctx(), {"days": 1})


# Явные since/until переопределяют days и попадают в MCP-args
@pytest.mark.asyncio
async def test_explicit_window_overrides_days(monkeypatch):
    fake = _FakeClient({"data": [{"clicks": 1}]})
    _patch_client(monkeypatch, fake)
    await GetTrackerStatsTool().run(
        _ctx(), {"days": 7, "since": "2026-01-01", "until": "2026-01-31"}
    )
    assert fake.captured[1]["from"] == "2026-01-01"
    assert fake.captured[1]["to"] == "2026-01-31"


# since > until → ToolError (валидация окна)
@pytest.mark.asyncio
async def test_since_after_until(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(ToolError, match="позже"):
        await GetTrackerStatsTool().run(_ctx(), {"since": "2026-02-01", "until": "2026-01-01"})


# Нет engine в контексте → ToolError require_engine
@pytest.mark.asyncio
async def test_requires_engine(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(ToolError, match="engine"):
        await GetTrackerStatsTool().run(ToolContext(client_key="t", engine=None), {"days": 1})
