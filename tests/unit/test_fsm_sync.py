# -*- coding: utf-8 -*-
"""Unit tests for transactional FSM projection after a Meta mutation."""

from __future__ import annotations

import pytest

from core.meta_api.errors import MutationValidationError
from core.meta_api.fsm_sync import (
    _resolve_bulk_ad_toggle,
    sync_fsm_after_mutation_in_transaction,
)
from core.meta_api.mutations.bulk_status_change import BulkStatusChangeHandler
from core.meta_api.schemas import MetaMutationPayload


def _payload(
    kind: str, target_id: str = "23847001", params: dict | None = None
) -> MetaMutationPayload:
    return MetaMutationPayload(
        mutation_kind=kind,
        target_id=target_id,
        params=params or {},
        ad_account_id="123",
    )


class _TransactionalConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))


# ====================== _resolve_bulk_ad_toggle ======================


# Canonical bulk action=pause → disable по всем ad_ids
def test_resolve_bulk_short_pause() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle({"ad_ids": ["1", "2"], "action": "pause"})
    assert ids == ["1", "2"]
    assert is_enable is False


# Canonical action=activate → enable
def test_resolve_bulk_short_activate() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle({"ad_ids": ["3"], "action": "activate"})
    assert ids == ["3"]
    assert is_enable is True


# Мусорный action → не трогаем (пустой список)
def test_resolve_bulk_short_unknown_action_noop() -> None:
    ids, _ = _resolve_bulk_ad_toggle({"ad_ids": ["1"], "action": "delete"})
    assert ids == []


def test_resolve_bulk_rejects_removed_full_form() -> None:
    ids, _ = _resolve_bulk_ad_toggle({"object_ids": ["9"], "status": "PAUSED", "object_type": "ad"})
    assert ids == []


def test_bulk_handler_accepts_only_canonical_ad_contract() -> None:
    ids, status = BulkStatusChangeHandler._extract_params(
        {"ad_ids": ["2", "1", "2"], "action": "pause"}
    )
    assert ids == ["2", "1"]
    assert status == "PAUSED"

    with pytest.raises(MutationValidationError, match="ad_ids"):
        BulkStatusChangeHandler._extract_params(
            {"object_ids": ["1"], "status": "PAUSED", "object_type": "ad"}
        )


@pytest.mark.asyncio
async def test_transactional_pause_projects_only_stoppable_states() -> None:
    conn = _TransactionalConn()

    await sync_fsm_after_mutation_in_transaction(
        conn,
        _payload("pause_ad", target_id="555"),
        {"outcome": "CONFIRMED", "modified_ids": ["555"]},
    )

    sql, params = conn.calls[0]
    assert params == {"fbid": "555"}
    assert "alert_state = 'disabled'" in sql
    assert "warning_sent" in sql
    assert "stop_sent" in sql
    assert "claimed" in sql


@pytest.mark.asyncio
async def test_transactional_activation_cannot_normalize_newer_stop_generation() -> None:
    conn = _TransactionalConn()

    await sync_fsm_after_mutation_in_transaction(
        conn,
        _payload("activate_ad", target_id="777"),
        {"outcome": "CONFIRMED", "modified_ids": ["777"]},
    )

    sql, params = conn.calls[0]
    assert params == {"fbid": "777"}
    assert "alert_state = 'disabled'" in sql
    assert "warning_sent" not in sql
    assert "stop_sent" not in sql
    assert "claimed" not in sql


@pytest.mark.asyncio
async def test_transactional_bulk_projects_only_confirmed_ids() -> None:
    conn = _TransactionalConn()

    await sync_fsm_after_mutation_in_transaction(
        conn,
        _payload(
            "bulk_status_change",
            params={"ad_ids": ["1", "2", "3"], "action": "activate"},
        ),
        {"outcome": "CONFIRMED", "modified_ids": ["1", "3"]},
    )

    assert [params["fbid"] for _, params in conn.calls] == ["1", "3"]


@pytest.mark.asyncio
async def test_transactional_bulk_requires_explicit_confirmed_ids() -> None:
    conn = _TransactionalConn()

    with pytest.raises(ValueError, match="modified_ids is required"):
        await sync_fsm_after_mutation_in_transaction(
            conn,
            _payload(
                "bulk_status_change",
                params={"ad_ids": ["1", "2"], "action": "activate"},
            ),
            {"outcome": "CONFIRMED"},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_transactional_non_status_mutation_is_noop() -> None:
    conn = _TransactionalConn()

    await sync_fsm_after_mutation_in_transaction(
        conn,
        _payload("duplicate_adset_structure"),
        {"outcome": "CONFIRMED", "modified_ids": ["42"]},
    )

    assert conn.calls == []
