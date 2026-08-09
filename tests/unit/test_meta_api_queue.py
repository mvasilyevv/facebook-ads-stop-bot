# -*- coding: utf-8 -*-
"""Unit tests for meta mutation idempotency and queue locks."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.meta_api.queue import create_mutation_task, default_idempotency_key
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import infer_task_lane, is_money_changing_task


@pytest.mark.parametrize(
    ("mutation_kind", "requested_by", "expected_lane"),
    [
        ("pause_ad", "bot_auto_stop", "money"),
        ("pause_ad", "operator:web", "interactive"),
        ("activate_ad", "operator:web", "interactive"),
        ("bulk_status_change", "owner:test", "bulk"),
        ("duplicate_adset_structure", "owner:test", "bulk"),
    ],
)
def test_mutation_lane_registry_isolates_automatic_pause_from_owner_actions(
    mutation_kind: str,
    requested_by: str,
    expected_lane: str,
) -> None:
    assert (
        infer_task_lane(
            "meta_api_mutation",
            {"mutation_kind": mutation_kind},
            requested_by=requested_by,
        )
        == expected_lane
    )


@pytest.mark.parametrize(
    ("task_type", "payload", "expected"),
    [
        ("meta_api_mutation", {"mutation_kind": "pause_ad"}, True),
        ("meta_api_mutation", {"mutation_kind": "activate_ad"}, True),
        (
            "meta_api_mutation",
            {"mutation_kind": "bulk_status_change", "params": {"action": "pause"}},
            True,
        ),
        (
            "meta_api_mutation",
            {"mutation_kind": "bulk_status_change", "params": {"action": "activate"}},
            True,
        ),
        (
            "meta_api_mutation",
            {"mutation_kind": "bulk_status_change", "params": {}},
            False,
        ),
        ("meta_api_mutation", {"mutation_kind": "duplicate_adset_structure"}, False),
        ("observer_scan", {"mutation_kind": "pause_ad"}, False),
        ("tracker_event_process", {"action": "read"}, False),
    ],
)
def test_money_notification_classification_is_semantic_not_lane(
    task_type: str,
    payload: dict[str, object],
    expected: bool,
) -> None:
    assert is_money_changing_task(task_type=task_type, payload=payload) is expected


# Одинаковые payload + requested_by + (нет salt) → одинаковый ключ (для дедупа).
def test_idempotency_key_stable() -> None:
    payload_a = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    payload_b = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    key_a = default_idempotency_key(payload_a, requested_by="user_42")
    key_b = default_idempotency_key(payload_b, requested_by="user_42")
    assert key_a == key_b


# Разный requested_by → разные ключи (auto-bot и user не должны конфликтовать).
def test_idempotency_key_diff_user() -> None:
    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="ad_1")
    key_user = default_idempotency_key(payload, requested_by="user_42")
    key_bot = default_idempotency_key(payload, requested_by="bot_auto")
    assert key_user != key_bot


# Ключ влезает в VARCHAR(128) ограничение БД.
def test_idempotency_key_length() -> None:
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="120999888777666",
        params={"reason": "test"},
    )
    key = default_idempotency_key(payload, requested_by="ai_assistant")
    assert len(key) <= 128


# Префикс meta: + mutation_kind + target_id виден в ключе — удобно искать в БД.
def test_idempotency_key_has_prefix() -> None:
    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="ad_999")
    key = default_idempotency_key(payload, requested_by="user")
    assert key.startswith("meta:pause_ad:123:ad_999:")


# Разный params → разный ключ.
def test_idempotency_key_params_matter() -> None:
    a = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="as_1",
        params={"daily": 1000},
    )
    b = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="as_1",
        params={"daily": 2000},
    )
    key_a = default_idempotency_key(a, requested_by="ai")
    key_b = default_idempotency_key(b, requested_by="ai")
    assert key_a != key_b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "pause_ad",
        "activate_ad",
    ],
)
async def test_single_ad_mutations_use_shared_target_lock(kind: str) -> None:
    """Pause/activate writers serialize on the same fb_ad_id advisory key."""
    captured: dict = {}

    async def fake_create_task(engine, **kwargs):
        captured.update(kwargs)
        return 1

    payload = MetaMutationPayload(
        ad_account_id="123", mutation_kind=kind, target_id="1200123456789"
    )
    with patch("core.meta_api.queue.create_task", fake_create_task):
        await create_mutation_task(object(), payload=payload, requested_by="test")

    assert captured["target_lock_key"] == "1200123456789"
    assert captured["target_lock_keys"] == ()


@pytest.mark.asyncio
async def test_mutation_forwards_telegram_origin_to_task_queue() -> None:
    """The command service may persist the Telegram actor with the money task."""
    captured: dict = {}

    async def fake_create_task(engine, **kwargs):
        captured.update(kwargs)
        return 1

    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="1200123456789",
    )
    with patch("core.meta_api.queue.create_task", fake_create_task):
        await create_mutation_task(
            object(),
            payload=payload,
            requested_by="telegram:operator",
            created_by_chat_id=777,
        )

    assert captured["created_by_chat_id"] == 777


@pytest.mark.asyncio
async def test_bulk_mutation_locks_every_ad_in_deterministic_order() -> None:
    """Bulk pause/activate cannot bypass per-ad recommendation serialization."""
    captured: dict = {}

    async def fake_create_task(engine, **kwargs):
        captured.update(kwargs)
        return 1

    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="bulk_status_change",
        target_id="bulk:3",
        params={"ad_ids": ["3", "1", "3", "2"], "action": "pause"},
    )
    with patch("core.meta_api.queue.create_task", fake_create_task):
        await create_mutation_task(object(), payload=payload, requested_by="test")

    assert captured["target_lock_key"] is None
    assert captured["target_lock_keys"] == ("1", "2", "3")
