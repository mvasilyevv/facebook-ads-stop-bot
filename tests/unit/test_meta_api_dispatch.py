# -*- coding: utf-8 -*-
"""Unit-тесты dispatch_mutation + реестра MUTATION_HANDLERS."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations import MUTATION_HANDLERS, dispatch_mutation
from core.meta_api.schemas import MUTATION_KINDS, MetaMutationPayload


# Реестр покрывает все объявленные MUTATION_KINDS — иначе будут KindError в worker.
def test_registry_covers_all_mutation_kinds() -> None:
    registered = set(MUTATION_HANDLERS.keys())
    declared = set(MUTATION_KINDS)
    missing = declared - registered
    extra = registered - declared
    assert not missing, f"Не зарегистрированы handlers для kinds: {missing}"
    assert not extra, f"Лишние handlers в реестре (не объявлены в schemas): {extra}"


# Все handlers имеют атрибут mutation_kind, совпадающий с ключом в реестре.
def test_each_handler_declares_its_kind() -> None:
    for key, handler in MUTATION_HANDLERS.items():
        assert hasattr(handler, "mutation_kind"), f"{handler!r} нет mutation_kind"
        assert handler.mutation_kind == key, (
            f"Несовпадение: handler.mutation_kind={handler.mutation_kind!r}, ключ={key!r}"
        )


# dispatch_mutation для известного kind вызывает execute этого handler'а.
@pytest.mark.asyncio
async def test_dispatch_calls_correct_handler_for_known_kind() -> None:
    fake_response = {"id": "23847001"}
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=fake_response)
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="23847001")

    result = await dispatch_mutation(client, payload)

    # PauseAdHandler действительно вызвал execute_graph_call с правильным endpoint.
    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["endpoint"] == "/23847001"
    assert result["success"] is True


# dispatch_mutation для неизвестного kind: NotImplementedError (worker → mark_failed).
@pytest.mark.asyncio
async def test_dispatch_unknown_kind_raises_not_implemented(monkeypatch) -> None:
    # MUTATION_KINDS жёстко валидирует kind в __post_init__, поэтому чтобы
    # сконструировать payload с неизвестным kind — патчим MUTATION_KINDS,
    # добавляя в неё "fake_kind" (но в MUTATION_HANDLERS его нет).
    from core.meta_api import schemas as schemas_pkg

    monkeypatch.setattr(schemas_pkg, "MUTATION_KINDS", frozenset({*MUTATION_KINDS, "fake_kind"}))

    client = AsyncMock()
    bad_payload = MetaMutationPayload.from_dict(
        {"mutation_kind": "fake_kind", "target_id": "23847001"}
    )

    with pytest.raises(NotImplementedError, match="fake_kind"):
        await dispatch_mutation(client, bad_payload)
