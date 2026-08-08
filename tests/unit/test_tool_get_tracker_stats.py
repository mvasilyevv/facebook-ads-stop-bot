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
    fake = _FakeClient(
        {
            "data": [
                {
                    "clicks": 100,
                    "registrations": 5,
                    "ftds": 2,
                    "revenue": "30.00",
                    "cost": "20.00",
                    "profit": "10.00",
                    "event_currency": "USD",
                }
            ]
        }
    )
    _patch_client(monkeypatch, fake)
    out = await GetTrackerStatsTool().run(_ctx(), {"days": 7})
    assert "итого" in out
    assert "Клики: 100" in out and "Реги: 5" in out
    assert "Provider FTD: 2" in out and "30.00 USD" in out
    assert "локально подтверждённый депозит" in out
    assert fake.captured[0] == "query_stats"
    assert fake.captured[1]["groups"] == ["event_currency"]


# Разрез по event_type: сортировка по FTD desc + ИТОГО суммирует ВСЕ строки (не только показанные)
@pytest.mark.asyncio
async def test_group_event_type_sorted_and_totals(monkeypatch):
    payload = {
        "data": [
            {
                "event_type": "CPA_HOLD",
                "clicks": 0,
                "registrations": 50,
                "ftds": 0,
                "revenue": 0,
                "profit": 0,
                "event_currency": "USD",
            },
            {
                "event_type": "CPA_ACCEPT",
                "clicks": 0,
                "registrations": 0,
                "ftds": 11,
                "revenue": 59,
                "profit": 59,
                "event_currency": "USD",
            },
            {
                "event_type": "SOURCE_CLICK",
                "clicks": 1334,
                "registrations": 0,
                "ftds": 0,
                "revenue": 0,
                "profit": 0,
                "event_currency": "USD",
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
    assert "59.00 USD" in out


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
    fake = _FakeClient({"data": [{"clicks": 1, "event_currency": "USD"}]})
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


@pytest.mark.asyncio
async def test_money_is_hidden_without_confirmed_currency(monkeypatch):
    fake = _FakeClient(
        {
            "data": [
                {
                    "clicks": 3,
                    "registrations": None,
                    "ftds": "bad",
                    "revenue": "99.99",
                    "cost": "12.00",
                    "profit": "87.99",
                }
            ]
        }
    )
    _patch_client(monkeypatch, fake)

    out = await GetTrackerStatsTool().run(_ctx(), {"days": 1})

    assert "Клики: 3" in out
    assert "Реги: —" in out
    assert "Provider FTD: —" in out
    assert "99.99" not in out
    assert "mixed/unknown currency" in out
    assert "$" not in out


@pytest.mark.asyncio
async def test_mixed_currency_never_sums_money(monkeypatch):
    fake = _FakeClient(
        {
            "data": [
                {
                    "clicks": 1,
                    "registrations": 0,
                    "ftds": 0,
                    "revenue": "10.00",
                    "cost": "1.00",
                    "profit": "9.00",
                    "event_currency": "USD",
                },
                {
                    "clicks": 2,
                    "registrations": 0,
                    "ftds": 0,
                    "revenue": "20.00",
                    "cost": "2.00",
                    "profit": "18.00",
                    "event_currency": "EUR",
                },
            ]
        }
    )
    _patch_client(monkeypatch, fake)

    out = await GetTrackerStatsTool().run(_ctx(), {"days": 1})

    assert "Клики: 3" in out
    assert "30.00" not in out
    assert "mixed/unknown currency" in out


@pytest.mark.asyncio
async def test_currency_exponent_controls_tracker_money_precision(monkeypatch):
    fake = _FakeClient(
        {
            "data": [
                {
                    "clicks": 1,
                    "registrations": 1,
                    "ftds": 1,
                    "revenue": "3.125",
                    "cost": "1.000",
                    "profit": "2.125",
                    "event_currency": "KWD",
                }
            ]
        }
    )
    _patch_client(monkeypatch, fake)

    out = await GetTrackerStatsTool().run(_ctx(), {"days": 1})

    assert "3.125 KWD" in out
    assert "1.000 KWD" in out
    assert "2.125 KWD" in out
