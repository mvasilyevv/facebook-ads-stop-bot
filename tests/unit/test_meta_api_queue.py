# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.queue — pure-функция default_idempotency_key."""

from __future__ import annotations

from core.meta_api.queue import default_idempotency_key
from core.meta_api.schemas import MetaMutationPayload


# Одинаковые payload + requested_by + (нет salt) → одинаковый ключ (для дедупа).
def test_idempotency_key_stable() -> None:
    payload_a = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    payload_b = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    key_a = default_idempotency_key(payload_a, requested_by="user_42")
    key_b = default_idempotency_key(payload_b, requested_by="user_42")
    assert key_a == key_b


# Разный requested_by → разные ключи (auto-bot и user не должны конфликтовать).
def test_idempotency_key_diff_user() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_1")
    key_user = default_idempotency_key(payload, requested_by="user_42")
    key_bot = default_idempotency_key(payload, requested_by="bot_auto")
    assert key_user != key_bot


# Salt делает каждый ключ уникальным (для DRAFT — каждый новый draft).
def test_idempotency_key_with_salt() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_1")
    key_1 = default_idempotency_key(payload, requested_by="ai", salt="2026-05-27T10:00:00")
    key_2 = default_idempotency_key(payload, requested_by="ai", salt="2026-05-27T10:01:00")
    assert key_1 != key_2


# Ключ влезает в VARCHAR(128) ограничение БД.
def test_idempotency_key_length() -> None:
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="120999888777666",
        params={"daily_budget_cents": 5000, "currency": "USD"},
    )
    key = default_idempotency_key(payload, requested_by="ai_assistant")
    assert len(key) <= 128


# Префикс meta: + mutation_kind + target_id виден в ключе — удобно искать в БД.
def test_idempotency_key_has_prefix() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_999")
    key = default_idempotency_key(payload, requested_by="user")
    assert key.startswith("meta:pause_ad:ad_999:")


# Разный params → разный ключ.
def test_idempotency_key_params_matter() -> None:
    a = MetaMutationPayload(
        mutation_kind="set_adset_budget", target_id="as_1", params={"daily": 1000}
    )
    b = MetaMutationPayload(
        mutation_kind="set_adset_budget", target_id="as_1", params={"daily": 2000}
    )
    key_a = default_idempotency_key(a, requested_by="ai")
    key_b = default_idempotency_key(b, requested_by="ai")
    assert key_a != key_b
