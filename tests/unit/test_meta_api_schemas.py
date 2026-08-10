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
    assert restored.ad_account_id == "555"


# Неизвестный mutation_kind должен бросить ValueError на конструкции.
def test_payload_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="Неизвестный mutation_kind"):
        MetaMutationPayload(
            ad_account_id="123",
            mutation_kind="self_destruct",
            target_id="123",
        )


# Все объявленные MUTATION_KINDS принимаются.
def test_all_mutation_kinds_accepted() -> None:
    for kind in MUTATION_KINDS:
        payload = MetaMutationPayload(ad_account_id="123", mutation_kind=kind, target_id="123")
        assert payload.mutation_kind == kind


def test_from_dict_rejects_missing_account_identity() -> None:
    with pytest.raises(KeyError, match="ad_account_id"):
        MetaMutationPayload.from_dict({"mutation_kind": "pause_ad", "target_id": "999"})


@pytest.mark.parametrize("account_id", [None, "", "act_", "abc", True])
def test_payload_rejects_invalid_account_identity(account_id: object) -> None:
    with pytest.raises(ValueError, match="explicit numeric account id"):
        MetaMutationPayload(
            mutation_kind="pause_ad",
            target_id="999",
            ad_account_id=account_id,  # type: ignore[arg-type]
        )
