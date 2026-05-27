# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.schemas — frozen dataclasses + валидация."""

from __future__ import annotations

import pytest

from core.meta_api.schemas import MUTATION_KINDS, MetaMutationPayload


# Round-trip: to_dict → from_dict сохраняет всё содержимое.
def test_payload_roundtrip() -> None:
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="120203040506",
        params={"reason": "manual"},
        ad_account_id="act_555",
    )
    data = payload.to_dict()
    restored = MetaMutationPayload.from_dict(data)
    assert restored == payload


# Неизвестный mutation_kind должен бросить ValueError на конструкции.
def test_payload_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="Неизвестный mutation_kind"):
        MetaMutationPayload(
            mutation_kind="self_destruct",
            target_id="123",
        )


# Все объявленные MUTATION_KINDS принимаются.
def test_all_mutation_kinds_accepted() -> None:
    for kind in MUTATION_KINDS:
        payload = MetaMutationPayload(mutation_kind=kind, target_id="123")
        assert payload.mutation_kind == kind


# from_dict терпит отсутствующие optional поля.
def test_from_dict_partial() -> None:
    payload = MetaMutationPayload.from_dict({"mutation_kind": "pause_ad", "target_id": "999"})
    assert payload.params == {}
    assert payload.ad_account_id is None
