from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import apps.api.routers.v1.tma as tma_router
from apps.api.main import create_app
from apps.api.routers.v1.tma import (
    TmaNavigationResolveRequest,
    TmaPrincipal,
    resolve_tma_navigation,
)
from core.telegram.navigation_tokens import NavigationTarget


def test_tma_navigation_contract_is_typed() -> None:
    operation = create_app().openapi()["paths"]["/api/tma/navigation/resolve"]["post"]

    assert operation["requestBody"]["required"] is True
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TmaNavigationResolveResponse"
    }


@pytest.mark.asyncio
async def test_tma_navigation_resolves_only_after_verified_principal(monkeypatch) -> None:
    consume = AsyncMock(return_value=NavigationTarget(kind="incident", target_id="incident-1"))
    monkeypatch.setattr(tma_router, "consume_navigation_token", consume)
    principal = TmaPrincipal(
        telegram_user_id=42,
        role="owner",
        chat_id=42,
        bot_generation=1,
    )
    engine = AsyncMock()

    response = await resolve_tma_navigation(
        TmaNavigationResolveRequest(token="N" * 22),
        principal,
        engine,
    )

    assert response.target_kind == "incident"
    assert response.target_id == "incident-1"
    consume.assert_awaited_once_with(
        engine,
        raw_token="N" * 22,
        telegram_user_id=42,
    )


@pytest.mark.asyncio
async def test_tma_navigation_does_not_disclose_invalid_capability(monkeypatch) -> None:
    monkeypatch.setattr(tma_router, "consume_navigation_token", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as caught:
        await resolve_tma_navigation(
            TmaNavigationResolveRequest(token="N" * 22),
            TmaPrincipal(
                telegram_user_id=42,
                role="owner",
                chat_id=42,
                bot_generation=1,
            ),
            AsyncMock(),
        )

    assert caught.value.status_code == 404
