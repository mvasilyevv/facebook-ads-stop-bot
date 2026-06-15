# -*- coding: utf-8 -*-
"""Unit: мульти-кабинет M4 — mutations роутятся во вкладку своего кабинета.

Каждый handler обязан прокинуть payload.ad_account_id в client.execute_graph_call —
browser-agent исполнит fetch из вкладки этого кабинета. None → legacy primary-вкладка.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.meta_api.mutations import dispatch_mutation
from core.meta_api.schemas import MetaMutationPayload


def _client(response: dict | None = None) -> MagicMock:
    """Фейковый MetaApiClient: записывает kwargs вызова."""
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value=response or {"success": True})
    return client


# pause_ad: кабинет из payload доходит до execute_graph_call (роутинг вкладки).
@pytest.mark.asyncio
async def test_pause_ad_routes_to_cabinet() -> None:
    client = _client()
    payload = MetaMutationPayload(
        mutation_kind="pause_ad", target_id="123456", params={}, ad_account_id="111"
    )

    await dispatch_mutation(client, payload)

    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["ad_account_id"] == "111"
    assert kwargs["endpoint"] == "/123456"


# activate_ad: то же самое для enable-пути.
@pytest.mark.asyncio
async def test_activate_ad_routes_to_cabinet() -> None:
    client = _client()
    payload = MetaMutationPayload(
        mutation_kind="activate_ad", target_id="654321", params={}, ad_account_id="222"
    )

    await dispatch_mutation(client, payload)

    assert client.execute_graph_call.call_args.kwargs["ad_account_id"] == "222"


# Без кабинета (legacy/bulk) — ad_account_id=None, исполнение с primary-вкладки.
@pytest.mark.asyncio
async def test_pause_ad_without_cabinet_passes_none() -> None:
    client = _client()
    payload = MetaMutationPayload(
        mutation_kind="pause_ad", target_id="123456", params={}, ad_account_id=None
    )

    await dispatch_mutation(client, payload)

    assert client.execute_graph_call.call_args.kwargs["ad_account_id"] is None
