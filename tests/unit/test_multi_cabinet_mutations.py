# -*- coding: utf-8 -*-
"""Unit: mutations всегда исполняются через явно заданный кабинет."""

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


def test_pause_ad_without_cabinet_is_rejected_before_dispatch() -> None:
    with pytest.raises(ValueError, match="explicit numeric account id"):
        MetaMutationPayload(
            mutation_kind="pause_ad",
            target_id="123456",
            params={},
            ad_account_id=None,  # type: ignore[arg-type]
        )
