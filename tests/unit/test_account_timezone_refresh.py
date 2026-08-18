# -*- coding: utf-8 -*-
"""Контракт фонового refresh снимков кабинета."""

from __future__ import annotations

import inspect
import logging

import pytest

import core.meta_api.account_tz as account_tz


class _FakeFence:
    """Заглушка BrowserOperationFence: аренда всегда наша и не теряется."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeFence":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def assert_held(self) -> None:
        return None


# Пустой ответ Meta раньше проглатывался молча: снимка нет, залив заблокирован,
# а в логе ни строки — причину было видно только глазами в интерфейсе.
@pytest.mark.asyncio
async def test_empty_meta_answer_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    async def _accounts(_engine):
        return ["2108857220005012"]

    async def _fetch(_client, _account_id):
        return account_tz.FetchedAccountContext(timezone_name=None, currency=None)

    async def _persist(*_args, **_kwargs):
        raise AssertionError("пустой контекст записывать нечем")

    monkeypatch.setattr(account_tz, "resolve_scan_account_ids", _accounts)
    monkeypatch.setattr(account_tz, "fetch_account_context", _fetch)
    monkeypatch.setattr(account_tz, "persist_account_context", _persist)
    monkeypatch.setattr(account_tz, "BrowserOperationFence", _FakeFence)

    with caplog.at_level(logging.WARNING, logger=account_tz.logger.name):
        updated = await account_tz.refresh_account_timezones(object(), object())

    assert updated == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any("2108857220005012" in message for message in messages)
    assert any("пояс" in message or "валют" in message for message in messages)


# Кабинеты берутся из конфигурации офферов, а не из следов сканов. Каталог
# отсканированных кампаний пуст, пока выключено сканирование, и новый кабинет
# не мог получить снимок НИКОГДА: визард требует контекст, контекст обновлялся
# только для кабинетов с уже отсканированными кампаниями, а их неоткуда взять
# (прод, 17.08.2026 — таблица снимков пуста при живом канале и живых данных).
@pytest.mark.asyncio
async def test_refresh_scope_comes_from_offers(monkeypatch) -> None:
    seen: list[str] = []

    async def _configured(_engine):
        return ["2108857220005012", "3570379159805007"]

    async def _fetch(_client, account_id):
        seen.append(account_id)
        return account_tz.FetchedAccountContext(
            timezone_name="America/Dawson_Creek", currency="USD"
        )

    async def _persist(_engine, *, account_id, timezone_name, currency):
        return True

    monkeypatch.setattr(account_tz, "resolve_scan_account_ids", _configured)
    monkeypatch.setattr(account_tz, "fetch_account_context", _fetch)
    monkeypatch.setattr(account_tz, "persist_account_context", _persist)
    monkeypatch.setattr(account_tz, "BrowserOperationFence", _FakeFence)

    updated = await account_tz.refresh_account_timezones(object(), object())

    assert updated == 2
    assert seen == ["2108857220005012", "3570379159805007"]


def test_account_tz_never_derives_scope_from_scan_results() -> None:
    """В модуле не должно остаться ни одного имени таблицы сканов."""
    source = inspect.getsource(account_tz)
    for table in ("fb_campaigns", "fb_adsets", "ad_metrics"):
        assert table not in source


def test_scan_derived_scope_helper_is_gone() -> None:
    # Имя собрано из кусков: сплошное переименование патчей не должно случайно
    # переписать этот assert и превратить его в проверку живого резолвера.
    assert not hasattr(account_tz, "active_" + "account_ids")
